#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
新闻搜索器 — 百度千帆 + Tavily 双引擎
定时遍历活跃搜索源，批量采集新闻并入库
支持对百度结果自动抓取全文
"""

import asyncio
import json
import logging
import re
import time
from collections import deque
from datetime import datetime
from typing import List, Dict, Optional, Callable
from concurrent.futures import ThreadPoolExecutor

import requests

from utils import news_store
from utils.settings_manager import settings_manager
from utils.newsnow_fetcher import fetch_newsnow
from utils.douyin_fetcher import fetch_douyin_hot_topics

logger = logging.getLogger(__name__)

# HTTP 超时配置
_BAIDU_TIMEOUT = 30
_TAVILY_TIMEOUT = 60
_DOUBAO_TIMEOUT = 30
_FETCH_TIMEOUT = 15
_FETCH_CONCURRENCY = 4  # 并发抓取全文的线程数


# ── 正文抓取 ─────────────────────────────────────────────────

def fetch_article_content(url: str) -> str:
    """
    抓取 URL 页面，提取正文纯文本
    使用 BeautifulSoup，优先提取 <article>/<main> 内容
    """
    if not url:
        return ""
    try:
        from bs4 import BeautifulSoup

        resp = requests.get(url, timeout=_FETCH_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        resp.raise_for_status()

        # 检测编码
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"

        soup = BeautifulSoup(resp.text, "lxml")

        # 移除不需要的标签
        for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                                   "aside", "iframe", "noscript", "form"]):
            tag.decompose()

        # 优先提取 article / main / 含新闻 class 的容器
        body = None
        for selector in [
            soup.find("article"),
            soup.find("main"),
            soup.find(class_=re.compile(r"article|content|post|detail|news|text|body", re.I)),
            soup.find(id=re.compile(r"article|content|post|detail|news|text|body", re.I)),
        ]:
            if selector:
                body = selector
                break

        if not body:
            body = soup.find("body") or soup

        # 提取段落文本
        paragraphs = body.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6"])
        text_parts = []
        for p in paragraphs:
            t = p.get_text(strip=True)
            if len(t) > 15:  # 过滤太短的段落（导航/广告）
                text_parts.append(t)

        content = "\n\n".join(text_parts)

        # 如果提取结果太短，fallback 到全文
        if len(content) < 100:
            content = body.get_text(separator="\n", strip=True)

        # 清理多余空行
        content = re.sub(r"\n{3,}", "\n\n", content).strip()

        logger.info("Fetched article content: %s (%d chars)", url[:60], len(content))
        return content

    except requests.exceptions.Timeout:
        logger.warning("Timeout fetching: %s", url[:60])
        return ""
    except Exception as e:
        logger.warning("Failed to fetch %s: %s", url[:60], e)
        return ""


def enrich_items_with_content(items: List[Dict]) -> List[Dict]:
    """
    对没有 full_text 的条目，并发抓取全文
    返回更新后的 items
    """
    need_fetch = [item for item in items
                  if item.get("url") and not item.get("full_text")]

    if not need_fetch:
        return items

    logger.info("Enriching %d items with full content (concurrency=%d)",
                len(need_fetch), _FETCH_CONCURRENCY)

    urls = [item["url"] for item in need_fetch]
    with ThreadPoolExecutor(max_workers=_FETCH_CONCURRENCY) as pool:
        contents = list(pool.map(fetch_article_content, urls))

    for item, content in zip(need_fetch, contents):
        if content:
            item["full_text"] = content

    fetched_count = sum(1 for item in need_fetch if item.get("full_text"))
    logger.info("Enriched %d/%d items with full content", fetched_count, len(need_fetch))
    return items


def fetch_single_item_content(item_id: int) -> Optional[str]:
    """
    抓取单条新闻的全文并更新数据库
    返回抓取到的全文文本
    """
    item = news_store.get_news_item(item_id)
    if not item:
        return None

    url = item.get("url", "")
    if not url:
        return None

    content = fetch_article_content(url)
    if not content:
        return None

    # 更新数据库
    news_store.update_news_item_full_text(item_id, content)
    return content


# ── 搜索引擎调用 ─────────────────────────────────────────────

def fetch_baidu(query: str, recency: str = "week", max_results: int = 10) -> List[Dict]:
    """
    调用百度千帆 AI Search API
    返回标准化的新闻列表（content 字段本身包含完整正文）
    """
    api_key = settings_manager.get("baidu_api_key")
    if not api_key:
        logger.warning("Baidu API key not configured")
        return []

    try:
        resp = requests.post(
            "https://qianfan.baidubce.com/v2/ai_search/web_search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "messages": [{"role": "user", "content": query}],
                "search_source": "baidu_search_v2",
                "resource_type_filter": [{"type": "web", "top_k": max_results}],
                "search_recency_filter": recency,
            },
            timeout=_BAIDU_TIMEOUT,
        )
        data = resp.json()

        if "code" in data and data["code"] != 200:
            logger.error("Baidu API error: %s", data.get("message", data.get("code", "")))
            return []

        results = []
        for ref in data.get("references", []):
            # 百度 API 的 content 字段本身就包含完整正文
            content = ref.get("content", "") or ref.get("snippet", "")
            # authority_score + rerank_score 综合评分
            rerank = ref.get("rerank_score", 0) or 0
            authority = ref.get("authority_score", 0) or 0
            score = max(rerank, authority)
            results.append({
                "title": ref.get("title", ""),
                "url": ref.get("url", ""),
                "snippet": content[:500],
                "full_text": content,
                "published_date": ref.get("date", ""),
                "author": ref.get("website", ref.get("web_anchor", "")),
                "score": score,
            })

        logger.info("Baidu search '%s' returned %d results", query[:30], len(results))
        return results

    except requests.exceptions.Timeout:
        logger.error("Baidu API timeout for query: %s", query[:30])
        return []
    except Exception as e:
        logger.error("Baidu API error: %s", e)
        return []


def fetch_doubao(query: str, max_results: int = 10) -> List[Dict]:
    """
    调用豆包/火山引擎联网搜索 API（Custom 版 web 搜索）
    返回标准化的新闻列表（Content 字段含完整正文，Summary 含相关性摘要）
    """
    api_key = settings_manager.get("doubao_api_key")
    if not api_key:
        logger.warning("Doubao API key not configured")
        return []

    try:
        resp = requests.post(
            "https://open.feedcoopapi.com/search_api/web_search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "Query": query,
                "SearchType": "web",
                "Count": min(max_results, 50),  # 接口上限 50 条
                "NeedSummary": True,
                "TimeRange": "OneWeek",
                "Filter": {"NeedUrl": True},
            },
            timeout=_DOUBAO_TIMEOUT,
        )
        data = resp.json()

        # 错误响应：ResponseMetadata.Error.CodeN 存在表示失败
        meta = data.get("ResponseMetadata", {}) or {}
        err = meta.get("Error")
        if err:
            logger.error("Doubao API error: %s", err.get("Message", err.get("CodeN", "")))
            return []

        result = data.get("Result", {}) or {}
        web_results = result.get("WebResults", []) or []

        results = []
        for ref in web_results:
            # Summary 是与 query 相关的正文片段（500~1000字），优于 Snippet
            summary = ref.get("Summary", "") or ref.get("Snippet", "")
            content = ref.get("Content", "") or summary
            results.append({
                "title": ref.get("Title", ""),
                "url": ref.get("Url", ""),
                "snippet": summary[:500],
                "full_text": content,
                "published_date": ref.get("PublishTime", ""),
                "author": ref.get("SiteName", ""),
                "score": ref.get("RankScore", 0) or 0,
            })

        logger.info("Doubao search '%s' returned %d results", query[:30], len(results))
        return results

    except requests.exceptions.Timeout:
        logger.error("Doubao API timeout for query: %s", query[:30])
        return []
    except Exception as e:
        logger.error("Doubao API error: %s", e)
        return []


def fetch_tavily(query: str, topic: str = "news", days: int = 7,
                 max_results: int = 10, include_raw: bool = True) -> List[Dict]:
    """
    调用 Tavily Search API
    返回标准化的新闻列表（含全文）
    """
    api_key = settings_manager.get("tavily_api_key")
    if not api_key:
        logger.warning("Tavily API key not configured")
        return []

    try:
        payload = {
            "api_key": api_key,
            "query": query,
            "topic": topic,
            "days": days,
            "max_results": max_results,
            "include_raw_content": include_raw,
            "include_answer": False,
        }
        resp = requests.post(
            "https://api.tavily.com/search",
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=_TAVILY_TIMEOUT,
        )
        data = resp.json()

        if data.get("error"):
            logger.error("Tavily API error: %s", data["error"])
            return []

        results = []
        for r in data.get("results", []):
            raw = r.get("raw_content", "") or ""
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": (r.get("content", "") or "")[:500],
                "full_text": raw,
                "published_date": r.get("published_date", ""),
                "author": "",
                "score": r.get("score", 0),
            })
        logger.info("Tavily search '%s' returned %d results", query[:30], len(results))
        return results

    except requests.exceptions.Timeout:
        logger.error("Tavily API timeout for query: %s", query[:30])
        return []
    except Exception as e:
        logger.error("Tavily API error: %s", e)
        return []


def fetch_aihot(query: str = "", mode: str = "selected",
                category: str = "", since_days: int = 7,
                max_results: int = 30) -> List[Dict]:
    """
    调用 AI HOT 公开 REST API（免费匿名，无需 API Key）
    https://aihot.virxact.com — AI 行业新闻聚合
    返回标准化的新闻列表（含中文摘要）
    """
    _AIHOT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 aihot-skill/0.2.0")
    _AIHOT_TIMEOUT = 30

    try:
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "mode": mode if mode in ("selected", "all") else "selected",
            "take": min(max_results, 100),
            "since": since,
        }
        if query:
            params["q"] = query
        if category and category in ("ai-models", "ai-products", "industry", "paper", "tip"):
            params["category"] = category

        # 使用独立 session，禁用系统代理避免 SSL 超时
        sess = requests.Session()
        sess.trust_env = False
        resp = sess.get(
            "https://aihot.virxact.com/api/public/items",
            headers={"User-Agent": _AIHOT_UA},
            params=params,
            timeout=_AIHOT_TIMEOUT,
        )

        if resp.status_code == 429:
            logger.warning("AI HOT rate limited (429), slowing down")
            return []

        data = resp.json()
        items = data.get("items", [])

        results = []
        for item in items:
            summary = item.get("summary", "") or ""
            pub_at = item.get("publishedAt", "") or ""
            # AI HOT summary 即为中文摘要，作为 full_text 使用
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": summary[:500],
                "full_text": summary,
                "published_date": pub_at[:10] if pub_at else "",
                "author": item.get("source", ""),
                "score": 0,
            })

        logger.info("AI HOT search '%s' mode=%s returned %d results",
                    query[:30] if query else "(all)", mode, len(results))
        return results

    except requests.exceptions.Timeout:
        logger.error("AI HOT API timeout")
        return []
    except Exception as e:
        logger.error("AI HOT API error: %s", e)
        return []


# ── 搜索循环 ─────────────────────────────────────────────────

def run_search_cycle(log: Optional[Callable[[str], None]] = None):
    """
    遍历所有活跃搜索源，调用配置的搜索引擎，保存结果
    返回 (总源数, 总新增条目数)

    Args:
        log: 可选的日志回调（接收消息字符串）。传入时用于把进度写入搜索器内存缓冲。
    """
    def _log(msg: str):
        (log or (lambda m: logger.info(m)))(msg)

    sources = news_store.get_active_sources()
    if not sources:
        _log("搜索跳过：无活跃搜索源")
        return 0, 0

    cycle_start = time.time()
    _log(f"开始搜索 | 共 {len(sources)} 个活跃搜索源")

    total_saved = 0
    for idx, source in enumerate(sources, 1):
        source_id = source["id"]
        name = source.get("name", "")
        engines = json.loads(source.get("search_engines", '["baidu","tavily"]'))
        max_results = source.get("max_results", 10)

        src_start = time.time()
        saved = 0

        if "baidu" in engines:
            query = source.get("query_baidu", "")
            if query:
                recency = source.get("baidu_recency", "week")
                items = fetch_baidu(query, recency=recency, max_results=max_results)
                if items:
                    saved += news_store.save_news_items(source_id, items, "baidu")

        if "doubao" in engines:
            # 豆包/火山引擎联网搜索，复用百度关键词；百度关键词为空时回退到搜索源名称
            query = source.get("query_baidu", "") or source.get("name", "")
            if query:
                items = fetch_doubao(query, max_results=max_results)
                if items:
                    saved += news_store.save_news_items(source_id, items, "doubao")

        if "tavily" in engines:
            # Tavily 搜索 API（用 query_tavily 关键词）
            query = source.get("query_tavily", "")
            if query:
                topic = source.get("tavily_topic", "news")
                days = source.get("tavily_days", 7)
                items = fetch_tavily(query, topic=topic, days=days, max_results=max_results)
                if items:
                    saved += news_store.save_news_items(source_id, items, "tavily")

        if "aihot" in engines:
            query = source.get("query_aihot", "")
            aihot_mode = source.get("aihot_mode", "selected")
            items = fetch_aihot(query=query, mode=aihot_mode, max_results=max_results)
            if items:
                saved += news_store.save_news_items(source_id, items, "aihot")

        if "newsnow" in engines:
            # newsnow 抓取多平台热榜（不需要 query，直接抓全量）
            platforms_str = source.get("query_aihot", "")  # 复用 query_aihot 字段存平台列表
            if platforms_str and platforms_str.strip():
                platforms = [p.strip() for p in platforms_str.split(",") if p.strip()]
                items = fetch_newsnow(sources=platforms, max_per_source=max_results)
            else:
                items = fetch_newsnow(max_per_source=max_results)
            if items:
                # newsnow 的每个平台用不同的 source_engine，需要分组保存
                from collections import defaultdict
                by_engine = defaultdict(list)
                for item in items:
                    by_engine[item["source_engine"]].append(item)
                for engine_name, engine_items in by_engine.items():
                    saved += news_store.save_news_items(source_id, engine_items, engine_name)

        if "douyin" in engines:
            # 抖音创作者中心热门话题（CDP方式）
            items = fetch_douyin_hot_topics(max_results=max_results)
            if items:
                saved += news_store.save_news_items(source_id, items, "douyin_creator")

        total_saved += saved
        elapsed = time.time() - src_start
        _log(f"[{idx}/{len(sources)}] {name} 搜索结束 | 新增={saved}条 | 耗时={elapsed:.1f}s")

    elapsed = time.time() - cycle_start
    _log(f"搜索完成 | 共 {len(sources)} 个搜索源 | 新增 {total_saved} 条 | 总耗时={elapsed:.1f}s")
    return len(sources), total_saved


def run_single_source_search(source_id: int) -> int:
    """对单个搜索源执行一次搜索，返回新增条目数"""
    source = news_store.get_news_source(source_id)
    if not source:
        logger.error("Source %d not found", source_id)
        return 0

    engines = json.loads(source.get("search_engines", '["baidu","tavily"]'))
    max_results = source.get("max_results", 10)
    saved = 0

    if "baidu" in engines:
        query = source.get("query_baidu", "")
        if query:
            recency = source.get("baidu_recency", "week")
            items = fetch_baidu(query, recency=recency, max_results=max_results)
            if items:
                saved += news_store.save_news_items(source_id, items, "baidu")

    if "doubao" in engines:
        # 豆包/火山引擎联网搜索，复用百度关键词；百度关键词为空时回退到搜索源名称
        query = source.get("query_baidu", "") or source.get("name", "")
        if query:
            items = fetch_doubao(query, max_results=max_results)
            if items:
                saved += news_store.save_news_items(source_id, items, "doubao")

    if "tavily" in engines:
        query = source.get("query_tavily", "")
        if query:
            topic = source.get("tavily_topic", "news")
            days = source.get("tavily_days", 7)
            items = fetch_tavily(query, topic=topic, days=days, max_results=max_results)
            if items:
                saved += news_store.save_news_items(source_id, items, "tavily")

    if "aihot" in engines:
        query = source.get("query_aihot", "")
        aihot_mode = source.get("aihot_mode", "selected")
        items = fetch_aihot(query=query, mode=aihot_mode, max_results=max_results)
        if items:
            saved += news_store.save_news_items(source_id, items, "aihot")

    if "newsnow" in engines:
        platforms_str = source.get("query_aihot", "")
        if platforms_str and platforms_str.strip():
            platforms = [p.strip() for p in platforms_str.split(",") if p.strip()]
            items = fetch_newsnow(sources=platforms, max_per_source=max_results)
        else:
            items = fetch_newsnow(max_per_source=max_results)
        if items:
            from collections import defaultdict
            by_engine = defaultdict(list)
            for item in items:
                by_engine[item["source_engine"]].append(item)
            for engine_name, engine_items in by_engine.items():
                saved += news_store.save_news_items(source_id, engine_items, engine_name)

    if "douyin" in engines:
        items = fetch_douyin_hot_topics(max_results=max_results)
        if items:
            saved += news_store.save_news_items(source_id, items, "douyin_creator")

    return saved


# ── 后台定时循环（单例）─────────────────────────────────────────

class NewsSearcher:
    """新闻搜索定时循环单例（支持间隔模式 / 定时模式）"""

    _instance = None
    _task: Optional[asyncio.Task] = None
    _running = False
    _last_search_time: int = 0
    _last_search_results: str = ""
    _last_new_count: int = 0
    _last_source_count: int = 0
    _next_run_at: Optional[float] = None      # 下次预计运行时间戳（用于倒计时）
    # 内存日志缓冲（最近 500 条）
    _log_buffer: deque = deque(maxlen=500)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        scheduled = self._get_scheduled_time()
        if scheduled:
            logger.info("News searcher started (scheduled=%s)", scheduled)
        else:
            logger.info("News searcher started (interval=%ds)", self._get_interval())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._next_run_at = None
        logger.info("News searcher stopped")

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
        getattr(logger, level.lower())(message)

    def get_logs(self, limit: int = 200) -> List[Dict]:
        """获取最近的日志条目"""
        logs = list(self._log_buffer)
        if limit and len(logs) > limit:
            logs = logs[-limit:]
        return logs

    def _get_interval(self) -> int:
        """动态获取搜索间隔（秒）"""
        return max(settings_manager.get_int("news_search_interval", 21600), 60)

    def _get_scheduled_time(self) -> str:
        """获取定时执行时间（HH:MM），为空则按间隔执行"""
        return settings_manager.get("news_scheduled_time", "").strip()

    def _calc_sleep_seconds(self) -> float:
        """
        计算下一次搜索的等待秒数。
        1. 设置了定时时间（HH:MM）→ 计算到下一个该时间点的秒数（兜底 86400s）
        2. 未设置 → 使用间隔
        """
        scheduled = self._get_scheduled_time()
        if scheduled:
            try:
                parts = scheduled.split(":")
                hour, minute = int(parts[0]), int(parts[1])
                from datetime import timedelta
                now = datetime.now()
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                sleep_secs = (target - now).total_seconds()
                backup = 86400
                if sleep_secs <= 0 or sleep_secs > backup:
                    logger.warning("News scheduled sleep %ds out of range, using backup %d",
                                   sleep_secs, backup)
                    return float(backup)
                logger.info("Next scheduled news search at %s (in %ds)",
                            target.strftime("%Y-%m-%d %H:%M"), int(sleep_secs))
                return sleep_secs
            except (ValueError, IndexError) as e:
                logger.warning("Invalid news scheduled time '%s': %s, falling back to interval",
                               scheduled, e)
        return float(self._get_interval())

    async def _run_cycle(self):
        """执行一次搜索并更新状态/日志，返回 (源数, 新增条数)"""
        loop = asyncio.get_event_loop()
        source_count, item_count = await loop.run_in_executor(
            None, lambda: run_search_cycle(log=lambda m: self._log("INFO", m))
        )
        self._last_search_time = int(time.time())
        self._last_source_count = source_count
        self._last_new_count = item_count
        self._last_search_results = f"{source_count} 个搜索源，新增 {item_count} 条新闻"
        return source_count, item_count

    async def _loop(self):
        """后台循环：支持间隔模式 / 定时模式"""
        first = True
        while self._running:
            # 定时模式：首次启动先等待到定时时间，不立即搜索
            if first and self._get_scheduled_time():
                first = False
                sleep_secs = self._calc_sleep_seconds()
                self._next_run_at = time.time() + sleep_secs
                self._log("INFO", f"定时模式，等待 {int(sleep_secs)}s 后执行首次搜索")
                await asyncio.sleep(sleep_secs)
                continue
            first = False
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("News search cycle error: %s", e, exc_info=True)
                self._log("ERROR", f"搜索周期异常: {e}")
            sleep_secs = self._calc_sleep_seconds()
            self._next_run_at = time.time() + sleep_secs
            try:
                await asyncio.sleep(sleep_secs)
            except asyncio.CancelledError:
                break

    async def search_now(self):
        """手动触发一次搜索，返回 (源数, 新增条数)"""
        source_count, item_count = await self._run_cycle()
        # 手动触发后，重新计算下次运行时间供倒计时显示
        sleep_secs = self._calc_sleep_seconds()
        self._next_run_at = time.time() + sleep_secs
        return source_count, item_count

    def get_status(self) -> Dict:
        """获取搜索器状态"""
        interval = self._get_interval()
        scheduled = self._get_scheduled_time()
        db_status = news_store.get_search_status()
        return {
            "running": self._running,
            "mode": "scheduled" if scheduled else "interval",
            "interval_seconds": interval,
            "scheduled_time": scheduled,
            "next_run_at": self._next_run_at,
            "last_search_at": self._last_search_time,
            "last_search_results": self._last_search_results,
            "last_new_count": self._last_new_count,
            **db_status,
        }


# 全局单例
news_searcher = NewsSearcher()
