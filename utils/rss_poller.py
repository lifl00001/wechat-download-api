#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
RSS 后台轮询器
定时通过公众号后台 API 拉取订阅号的最新文章列表并缓存到 SQLite。
仅获取标题、摘要、封面等元数据，不访问文章页面，零风控风险。
"""

import asyncio
import json
import logging
import os
import time
from collections import deque
from datetime import datetime
from typing import List, Dict, Optional

import httpx

from utils.auth_manager import auth_manager
from utils import rss_store
from utils.helpers import extract_article_info, parse_article_url, is_image_text_message, has_article_content, is_article_unavailable, get_unavailable_reason
from utils.http_client import fetch_page
from utils.settings_manager import settings_manager

logger = logging.getLogger(__name__)

# Legacy module-level constants kept for backward compat (routes/rss.py imports POLL_INTERVAL)
POLL_INTERVAL = int(os.getenv("RSS_POLL_INTERVAL", "3600"))
ARTICLES_PER_POLL = int(os.getenv("ARTICLES_PER_POLL", "10"))
FETCH_FULL_CONTENT = os.getenv("RSS_FETCH_FULL_CONTENT", "true").lower() == "true"


class WechatInvalidFakeidError(Exception):
    """
    [2026-05-18] 公众号在微信侧已失效（已注销/改名/重新注册）

    触发条件：appmsgpublish 接口返回 ret=200002 且 err_msg="invalid args"
    实测：任何 token+cookie 都无法访问，需要标记为永久失效
    """
    pass


class WechatLoginExpiredError(Exception):
    """
    [2026-07-05] 微信登录凭证已被服务端失效

    触发条件：appmsgpublish 接口返回 ret=200003，或 err_msg 含 "login"
    与本地 expire_time 无关 —— 微信服务端主动让 cookie 失效（异地登录、风控等）
    表现：所有公众号都返回"无文章"，实际是凭证已过期
    """
    pass


class WechatFreqControlError(Exception):
    """
    [2026-08-01] 微信接口频率限制（限频）

    触发条件：appmsgpublish 接口返回 ret=200013，err_msg="freq control"
    原因：短时间内对同一账号大量调用接口，触发微信服务端限流
    表现：所有公众号秒回"无文章"（耗时 0.1~0.4s），ret=200013
    处理：立即中断本轮轮询，避免继续打剩余号导致限频时间延长
    恢复：等待 30~60 分钟后微信自动解除，期间不要发起任何请求
    """
    pass


class RSSPoller:
    """后台轮询单例"""

    _instance = None
    _task: Optional[asyncio.Task] = None
    _running = False
    _polling = False  # 当前是否正在轮询中
    # [2026-05-15 OS-4] 共享 httpx.AsyncClient 避免每轮每 fakeid 都新建（省 DNS+TLS 握手）
    _http_client: Optional[httpx.AsyncClient] = None
    # 轮询状态追踪
    _last_poll_time: Optional[float] = None  # 上次轮询完成时间戳
    _last_new_count: int = 0                  # 上次轮询新增文章数
    _last_poll_message: str = ""              # 上次轮询结果摘要
    _login_expired: bool = False              # [2026-07-05] 凭证是否已被微信服务端失效
    # 内存日志缓冲（最近 500 条）
    _log_buffer: deque = deque(maxlen=500)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def start(self):
        # 如果正在运行且任务存活，直接返回
        if self._running and self._task and not self._task.done():
            return
        # 如果正在运行但任务已死（异常退出），先清理状态
        if self._running and self._task and self._task.done():
            logger.warning("RSS poller task died unexpectedly, restarting...")
            self._log("WARNING", "RSS 轮询任务异常退出，正在重启...")
            self._task = None
        elif not self._running:
            self._running = True
        # 创建长连接 client，连接池 + keep-alive 自动复用
        if not self._http_client:
            self._http_client = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            )
        self._task = asyncio.create_task(self._loop())
        scheduled = self._get_scheduled_time_safe()
        if scheduled:
            logger.info("RSS poller started (scheduled=%s)", scheduled)
        else:
            logger.info("RSS poller started (interval=%ds)", self._get_poll_interval_safe())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # 关闭共享 client
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None
        logger.info("RSS poller stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    def _log(self, level: str, message: str):
        """写入内存日志缓冲，同时输出到 Python logging"""
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "message": message,
        }
        self._log_buffer.append(entry)
        # 同步到标准 logging
        getattr(logger, level.lower())(message)

    def get_logs(self, limit: int = 200) -> List[Dict]:
        """获取最近的日志条目"""
        logs = list(self._log_buffer)
        if limit and len(logs) > limit:
            logs = logs[-limit:]
        return logs

    def _get_poll_interval(self) -> int:
        """动态获取轮询间隔（秒），优先从 settings_manager 读取"""
        return settings_manager.get_int("rss_poll_interval", POLL_INTERVAL)

    def _get_scheduled_time(self) -> str:
        """获取定时执行时间（HH:MM格式），为空则按间隔轮询"""
        return settings_manager.get("rss_scheduled_time", "").strip()

    def _get_scheduled_time_safe(self) -> str:
        """安全版本：捕获所有异常，返回空字符串"""
        try:
            return self._get_scheduled_time()
        except Exception as e:
            logger.error("Failed to get RSS scheduled time: %s", e)
            return ""

    def _get_poll_interval_safe(self) -> int:
        """安全版本：捕获所有异常，返回默认间隔"""
        try:
            return self._get_poll_interval()
        except Exception as e:
            logger.error("Failed to get RSS poll interval: %s", e)
            return POLL_INTERVAL

    def _get_articles_per_poll(self) -> int:
        """动态获取每次拉取文章数"""
        return settings_manager.get_int("articles_per_poll", ARTICLES_PER_POLL)

    def _get_fetch_full_content(self) -> bool:
        """动态获取是否获取完整内容"""
        return settings_manager.get_bool("rss_fetch_full_content", FETCH_FULL_CONTENT)

    def _calc_sleep_seconds(self) -> float:
        """
        计算下一次轮询的等待秒数。

        逻辑：
        1. 如果设置了定时执行时间（HH:MM），则计算到下一个该时间点的秒数
           - 若计算结果 <= 0 或 > 备用间隔，使用备用间隔兜底
        2. 如果没有设置定时执行时间，使用轮询间隔
        异常时返回默认间隔（1小时）
        """
        try:
            scheduled = self._get_scheduled_time_safe()

            if scheduled:
                try:
                    parts = scheduled.split(":")
                    hour, minute = int(parts[0]), int(parts[1])
                    from datetime import datetime, timedelta
                    now = datetime.now()
                    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    # 如果今天的执行时间已过，设为明天
                    if target <= now:
                        target += timedelta(days=1)
                    sleep_secs = (target - now).total_seconds()
                    backup = 86400  # 兜底：最大 24 小时
                    # 异常值兜底：超过 24 小时或 <= 0 时用兜底值
                    if sleep_secs <= 0 or sleep_secs > backup:
                        logger.warning("Scheduled sleep %ds out of range, using backup interval %ds",
                                       sleep_secs, backup)
                        return float(backup)
                    logger.info("Next scheduled poll at %s (in %ds)", target.strftime("%Y-%m-%d %H:%M"), int(sleep_secs))
                    return sleep_secs
                except (ValueError, IndexError) as e:
                    logger.warning("Invalid scheduled time format '%s': %s, falling back to interval", scheduled, e)

            return float(self._get_poll_interval_safe())
        except Exception as e:
            logger.error("Failed to calculate RSS sleep seconds: %s, using default 1h", e)
            return 3600.0  # 默认 1 小时

    async def _loop(self):
        first = True
        while self._running:
            # 定时模式：首次启动时先等待到定时时间，不立即轮询
            if first and self._get_scheduled_time_safe():
                first = False
                try:
                    sleep_secs = self._calc_sleep_seconds()
                    self._log("INFO", f"定时模式，等待 {int(sleep_secs)}s 后执行首次轮询")
                    await asyncio.sleep(sleep_secs)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("Failed to calculate initial RSS sleep: %s", e)
                    self._log("ERROR", f"计算初始等待时间失败: {e}")
                    # 使用默认间隔继续
                    sleep_secs = 3600.0
                    await asyncio.sleep(sleep_secs)
                continue
            first = False
            try:
                await self._poll_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("RSS poll cycle error: %s", e, exc_info=True)
                self._log("ERROR", f"RSS 轮询异常: {e}")
            # 计算下次运行时间，确保任何异常都不会杀死循环
            try:
                sleep_secs = self._calc_sleep_seconds()
            except Exception as e:
                logger.error("Failed to calculate RSS sleep seconds: %s, using default 1h", e)
                self._log("ERROR", f"计算下次运行时间失败: {e}")
                sleep_secs = 3600.0
            try:
                await asyncio.sleep(sleep_secs)
            except asyncio.CancelledError:
                break

    async def _poll_all(self):
        self._polling = True
        # [2026-07-05] 每轮开始先重置凭证失效标志（若本次又失效会在循环里重新置位）
        self._login_expired = False
        total_new = 0
        total_api_articles = 0
        poll_start = time.time()
        try:
            fakeids = rss_store.get_all_fakeids()
            if not fakeids:
                self._log("INFO", "轮询跳过：无订阅")
                self._last_poll_message = "无订阅"
                return

            creds = auth_manager.get_credentials()
            if not creds or not creds.get("token") or not creds.get("cookie"):
                self._log("WARNING", "轮询跳过：未登录")
                self._last_poll_message = "未登录，跳过轮询"
                return

            # 获取活跃黑名单
            blacklisted = set(rss_store.get_active_blacklist_fakeids())

            # 过滤掉黑名单中的公众号
            active_fakeids = [f for f in fakeids if f not in blacklisted]
            skipped = len(fakeids) - len(active_fakeids)
            total_count = len(fakeids)

            self._log("INFO", f"开始轮询 | 共 {total_count} 个公众号" +
                      (f"（{skipped} 个黑名单跳过）" if skipped > 0 else ""))

            for idx, fakeid in enumerate(active_fakeids, 1):
                sub = rss_store.get_subscription(fakeid)
                nickname = sub.get("nickname", fakeid[:8]) if sub else fakeid[:8]
                fakeid_start = time.time()
                try:
                    articles = await self._fetch_article_list(fakeid, creds)
                    api_count = len(articles) if articles else 0
                    total_api_articles += api_count

                    if articles and self._get_fetch_full_content():
                        # 获取完整文章内容
                        articles = await self._enrich_articles_content(fakeid, articles)

                    if articles:
                        # 轮询器拉取的文章标记为 'poll'
                        new_count = rss_store.save_articles(fakeid, articles, source='poll')
                        total_new += new_count
                        fakeid_elapsed = time.time() - fakeid_start
                        status = "成功" if new_count >= 0 else "无新文章"
                        self._log("INFO",
                            f"[{idx}/{total_count}] {nickname} 轮询结束"
                            f" | 状态={status} | API返回={api_count}篇"
                            f" | 新增={new_count}篇 | 耗时={fakeid_elapsed:.1f}s")
                    else:
                        fakeid_elapsed = time.time() - fakeid_start
                        self._log("INFO",
                            f"[{idx}/{total_count}] {nickname} 轮询结束"
                            f" | 状态=无文章 | 耗时={fakeid_elapsed:.1f}s")
                    rss_store.update_last_poll(fakeid)
                except WechatLoginExpiredError as e:
                    # [2026-07-05] 凭证已被微信服务端失效
                    # 第一个号命中就立即中断整个轮询 —— 凭证失效时所有号都会失败，没必要继续
                    self._login_expired = True
                    self._log("WARNING",
                        f"⚠️ [{idx}/{total_count}] {nickname} 拉取失败：微信登录已过期"
                        f"（ret={e}）—— 中止本轮轮询，请尽快重新扫码登录")
                    self._last_poll_message = (
                        f"轮询中止：微信登录已过期（在 {nickname} 处检测到），"
                        f"已轮询 {idx}/{total_count}，请重新扫码登录"
                    )
                    # 异步发通知（邮件 + webhook，不阻塞）
                    asyncio.create_task(self._notify_login_expired(nickname, idx, total_count))
                    break
                except WechatFreqControlError as e:
                    # [2026-08-01] 微信限频：立即中断整轮轮询
                    # 继续打剩下的号只会延长限频时间（每个号都会被秒拒）
                    self._log("WARNING",
                        f"⚠️ [{idx}/{total_count}] {nickname} 触发微信限频"
                        f"（ret=200013 freq control）—— 中止本轮轮询，"
                        f"请等待 30~60 分钟后再试，期间不要发起任何请求")
                    self._last_poll_message = (
                        f"轮询中止：微信接口限频（在 {nickname} 处检测到），"
                        f"已轮询 {idx}/{total_count}，请等待 30~60 分钟后重试"
                    )
                    break
                except WechatInvalidFakeidError as e:
                    # [2026-05-18] 同步 SaaS 修复：fakeid 在微信侧已失效，自动加入黑名单
                    self._log("WARNING",
                        f"[{idx}/{total_count}] {nickname} 已失效（注销/改名），加入黑名单")
                    try:
                        rss_store.add_to_blacklist(
                            fakeid, nickname=nickname, reason="invalid_fakeid",
                            note="[2026-05-18] 微信侧返回 invalid args，fakeid 已失效（注销/改名/重新注册）",
                        )
                    except Exception as bl_err:
                        self._log("WARNING", f"加入黑名单失败 {nickname}: {bl_err}")
                except Exception as e:
                    self._log("ERROR", f"[{idx}/{total_count}] {nickname} 轮询异常: {e}")
                # [2026-08-01] 分批轮询：每 10 个号为一批，批次间暂停 60 秒
                # 单号间隔从 3s 加大到 8s，模拟人工浏览节奏，降低触发微信限频的概率
                if idx % 10 == 0 and idx < len(active_fakeids):
                    self._log("INFO",
                        f"⏸️ 已轮询 {idx}/{total_count}，分批暂停 60 秒后继续"
                        f"（防止单 IP 高频请求触发微信限频）")
                    await asyncio.sleep(60)
                else:
                    await asyncio.sleep(8)

            elapsed = time.time() - poll_start
            self._last_new_count = total_new
            self._last_poll_message = (
                f"轮询完成 | 共 {total_count} 个公众号 | API返回 {total_api_articles} 篇"
                f" | 新增 {total_new} 篇 | 总耗时={elapsed:.1f}s"
            )
            self._log("INFO", self._last_poll_message)
        finally:
            self._polling = False
            self._last_poll_time = time.time()

    async def _notify_login_expired(self, nickname: str, idx: int, total: int):
        """[2026-07-05] 凭证失效时发邮件 + webhook 通知"""
        from utils.email_notifier import email_notifier
        from utils.webhook import webhook
        from datetime import datetime

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary = f"微信登录已过期 · {ts}"

        # 邮件（HTML）
        html = f"""
        <div style="font-family:'Microsoft YaHei',Arial,sans-serif;max-width:560px;margin:0 auto;
                    border:1px solid #ffa39e;border-radius:8px;overflow:hidden;">
          <div style="background:#fff1f0;color:#cf1322;padding:16px 20px;font-size:16px;font-weight:bold;">
            ⚠️ 微信公众号登录已过期
          </div>
          <div style="padding:20px;color:#333;line-height:1.8;font-size:14px;">
            <p>RSS 轮询在拉取公众号 <b>{nickname}</b> 时，检测到微信服务端已让凭证失效。</p>
            <table style="border-collapse:collapse;margin:12px 0;">
              <tr><td style="padding:4px 12px;color:#888;">检测时间</td><td>{ts}</td></tr>
              <tr><td style="padding:4px 12px;color:#888;">检测位置</td><td>{idx}/{total} {nickname}</td></tr>
              <tr><td style="padding:4px 12px;color:#888;">影响</td><td>本轮轮询已中止，所有公众号无法拉取</td></tr>
            </table>
            <p style="background:#e6f7ff;border-left:3px solid #1890ff;padding:10px 14px;margin:12px 0;">
              请尽快打开管理后台扫码重新登录：<br>
              <a href="http://localhost:5000/login.html">http://localhost:5000/login.html</a>
            </p>
          </div>
        </div>
        """
        await email_notifier.notify(summary, html)

        # webhook（企业微信等，已配置才发）
        await webhook.notify("login_expired", {
            "nickname": nickname,
            "detected_at": idx,
            "total": total,
            "message": f"RSS轮询检测到微信登录已过期（{nickname}），请重新扫码登录",
        })

    async def _fetch_article_list(self, fakeid: str, creds: Dict) -> List[Dict]:
        params = {
            "sub": "list",
            "search_field": "null",
            "begin": 0,
            "count": self._get_articles_per_poll(),
            "query": "",
            "fakeid": fakeid,
            "type": "101_1",
            "free_publish_type": 1,
            "sub_action": "list_ex",
            "token": creds["token"],
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://mp.weixin.qq.com/",
            "Cookie": creds["cookie"],
        }

        # [2026-05-15 OS-4] 使用共享 client，省 DNS+TLS 握手
        # 兜底：若 client 未初始化（理论不会发生），退回到每次新建
        if self._http_client is not None:
            resp = await self._http_client.get(
                "https://mp.weixin.qq.com/cgi-bin/appmsgpublish",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            result = resp.json()
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    "https://mp.weixin.qq.com/cgi-bin/appmsgpublish",
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                result = resp.json()

        base_resp = result.get("base_resp", {})
        if base_resp.get("ret") != 0:
            ret_code = base_resp.get("ret")
            err_msg = base_resp.get("err_msg", "")
            logger.warning("WeChat API error for %s: ret=%s err_msg=%r",
                           fakeid[:8], ret_code, err_msg)
            # [2026-07-05] 凭证失效检测：ret=200003 或 err_msg 含 "login"
            # 微信服务端主动让 cookie 失效（异地登录/风控），本地 expire_time 可能尚未到期
            # 必须在 200002 之前判定，避免被静默 return [] 当成"无文章"
            if ret_code == 200003 or "login" in err_msg.lower():
                raise WechatLoginExpiredError(
                    f"微信登录已过期: ret={ret_code}, err_msg={err_msg}"
                )
            # [2026-08-01] 限频检测：ret=200013 + "freq control"
            # 微信对短时间内大量调用接口触发限流，所有公众号都会被拒
            # 必须立即中断整轮轮询，否则只会延长限频时间
            if ret_code == 200013 or "freq control" in err_msg.lower():
                raise WechatFreqControlError(
                    f"微信接口限频: ret={ret_code}, err_msg={err_msg}"
                )
            # [2026-05-18] 同步 SaaS 修复：ret=200002 + "invalid args" → fakeid 已失效
            # 老代码统一返回空 → 静默失败，用户感受不到该号已注销
            # 现在：抛 WechatInvalidFakeidError 让调用方加入黑名单
            if ret_code == 200002 and "invalid arg" in err_msg.lower():
                raise WechatInvalidFakeidError(
                    f"fakeid {fakeid[:8]} 已失效（注销/改名）: {err_msg}"
                )
            return []

        publish_page = result.get("publish_page", {})
        if isinstance(publish_page, str):
            try:
                publish_page = json.loads(publish_page)
            except (json.JSONDecodeError, ValueError):
                return []

        if not isinstance(publish_page, dict):
            return []

        articles = []
        for item in publish_page.get("publish_list", []):
            publish_info = item.get("publish_info", {})
            if isinstance(publish_info, str):
                try:
                    publish_info = json.loads(publish_info)
                except (json.JSONDecodeError, ValueError):
                    continue
            if not isinstance(publish_info, dict):
                continue
            for a in publish_info.get("appmsgex", []):
                articles.append({
                    "aid": a.get("aid", ""),
                    "title": a.get("title", ""),
                    "link": a.get("link", ""),
                    "digest": a.get("digest", ""),
                    "cover": a.get("cover", ""),
                    "author": a.get("author", ""),
                    "publish_time": a.get("update_time", 0),
                })
        return articles

    async def poll_now(self):
        """手动触发一次轮询"""
        await self._poll_all()
    
    async def _enrich_articles_content(self, fakeid: str, articles: List[Dict]) -> List[Dict]:
        """
        批量获取文章完整内容（并发版）
        
        限制：最多获取 20 篇文章的完整内容（避免大量文章导致轮询过久）
        
        Args:
            articles: 文章列表（包含基本信息）
            
        Returns:
            enriched_articles: 包含完整内容的文章列表
        """
        from utils.article_fetcher import fetch_articles_batch
        from utils.content_processor import process_article_content
        
        # 提取所有文章链接
        article_links = [a.get("link", "") for a in articles if a.get("link")]
        
        if not article_links:
            return articles
        
        # 限制最多获取 20 篇（5个批次可能返回100+篇）
        max_fetch = 20
        if len(article_links) > max_fetch:
            logger.info("文章数 %d 篇超过限制，仅获取最近 %d 篇的完整内容", 
                       len(article_links), max_fetch)
            article_links = article_links[:max_fetch]
            articles = articles[:max_fetch]
        
        logger.info("开始批量获取 %d 篇文章的完整内容", len(article_links))
        
        # 获取微信凭证（从环境变量读取）
        wechat_token = os.getenv("WECHAT_TOKEN", "")
        wechat_cookie = os.getenv("WECHAT_COOKIE", "")
        
        results = await fetch_articles_batch(
            article_links, 
            max_concurrency=3, 
            timeout=60,
            wechat_token=wechat_token,
            wechat_cookie=wechat_cookie
        )
        
        # 处理结果并合并到原文章数据
        enriched = []
        for article in articles:
            link = article.get("link", "")
            if not link:
                enriched.append(article)
                continue
            
            html = results.get(link)
            if not html:
                logger.warning("Empty HTML: %s", link[:80])
                enriched.append(article)
                continue
            
            # [2026-05-18] 精确化验证码检测（之前用 "验证" 二字误伤大量正文含此字的文章）
            # 微信风控页特有标记：
            #   1. verifycode 出现在 URL/form/script 中（最强信号）
            #   2. "请输入图片中的字符" — 微信原版验证码提示文案
            #   3. "环境异常" — 微信明确风控提示（保留原检测）
            # 移除单纯的"验证"二字判断 — 文章正文里出现概率高，会导致 content 丢失
            html_lower = html.lower()
            verification_markers = (
                "verifycode" in html_lower
                or "请输入图片中的字符" in html
                or "环境异常" in html
            )
            if verification_markers:
                sub = rss_store.get_subscription(fakeid)
                nickname = sub.get("nickname", "") if sub else ""
                count = rss_store.increment_verification_count(fakeid, nickname)
                logger.warning("Verification triggered for %s (count=%d): %s",
                             fakeid[:8], count, link[:60])
                enriched.append(article)
                continue
            
            if is_article_unavailable(html):
                reason = get_unavailable_reason(html) or "unknown"
                logger.warning("Article permanently unavailable (%s): %s", reason, link[:80])
                article["content"] = f"<p>[unavailable] {reason}</p>"
                article["plain_content"] = f"[unavailable] {reason}"
                enriched.append(article)
                continue
            if not has_article_content(html):
                logger.warning("No content in HTML: %s", link[:80])
                enriched.append(article)
                continue
            
            try:
                # 使用 content_processor 处理文章内容（完美保持图文顺序）
                # 从环境变量读取网站URL,入库时代理图片(与SaaS版策略一致)
                site_url = os.getenv("SITE_URL", "http://localhost:5000").rstrip("/")
                result = process_article_content(html, proxy_base_url=site_url)
                
                # 合并到原文章数据
                article["content"] = result.get("content", "")
                article["plain_content"] = result.get("plain_content", "")
                
                # 如果原始数据没有作者，从 HTML 中提取
                if not article.get("author"):
                    from utils.helpers import extract_article_info, parse_article_url
                    article_info = extract_article_info(html, parse_article_url(link))
                    article["author"] = article_info.get("author", "")
                
                logger.info("Content fetched: %s... (%d chars, %d images)",
                           link[:50],
                           len(article["content"]), 
                           len(result.get("images", [])))
            except Exception as e:
                logger.error("Failed to process content for %s: %s", link[:80], str(e))
            
            enriched.append(article)
        
        return enriched


rss_poller = RSSPoller()
