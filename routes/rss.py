#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
RSS 订阅路由
订阅管理 + RSS XML 输出
"""

import csv
import io
import os
import time
import logging
from datetime import datetime, timezone
from html import escape as html_escape
from typing import Optional, List
import xml.etree.ElementTree as ET

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from utils import rss_store
from utils.rss_poller import rss_poller, POLL_INTERVAL
from utils.image_proxy import proxy_image_url
from utils.rss_streaming import (
    generate_single_rss_stream, 
    generate_historical_rss_stream,
    generate_aggregated_rss_stream,
    generate_category_rss_stream
)

logger = logging.getLogger(__name__)


def get_base_url(request: Request) -> str:
    """
    获取服务的基础 URL，优先使用环境变量 SITE_URL，
    支持反向代理（检测 X-Forwarded-Proto 和 X-Forwarded-Host）
    """
    # 优先使用配置的 SITE_URL
    site_url = os.getenv("SITE_URL", "").strip()
    if site_url:
        return site_url.rstrip("/")
    
    # 检测反向代理头部
    proto = request.headers.get("X-Forwarded-Proto", "http")
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host", "localhost:5000")
    
    return f"{proto}://{host}"

router = APIRouter()

# RSS 配置常量 - 动态限制策略
# [2026-05-06 优化] 根据场景设置不同默认值和上限，降低内存占用
#
# 核心区别：
# - 常规 RSS（单个/聚合/分类）：动态滚动更新，限制较小，节省内存
# - 历史 RSS：静态归档内容，一次性加载，上限较高，避免文章遗漏

RSS_SINGLE_DEFAULT = 30      # 单个公众号：默认 30，覆盖 6-15 天
RSS_SINGLE_MAX = 50          # 单个公众号：最大 50

RSS_AGGREGATED_DEFAULT = 4500    # 聚合 RSS：默认最大值，由窗口函数内部逻辑控制
RSS_AGGREGATED_MAX = 4500        # 聚合 RSS：最大 4500

RSS_CATEGORY_DEFAULT = 4500  # 分类 RSS：默认最大值，由窗口函数内部逻辑控制
RSS_CATEGORY_MAX = 4500      # 分类 RSS：最大 4500

RSS_HISTORICAL_DEFAULT = 500 # 历史 RSS：默认 500（付费内容，一次性加载）
RSS_HISTORICAL_MAX = 5000    # 历史 RSS：最大 5000（支持大量历史文章，避免遗漏）


# ── Pydantic models ──────────────────────────────────────

class SubscribeRequest(BaseModel):
    fakeid: str = Field(..., description="公众号 FakeID")
    nickname: str = Field("", description="公众号名称")
    alias: str = Field("", description="公众号微信号")
    head_img: str = Field("", description="头像 URL")


class SubscribeResponse(BaseModel):
    success: bool
    message: str = ""


class SubscriptionItem(BaseModel):
    fakeid: str
    nickname: str
    alias: str
    head_img: str
    created_at: int
    last_poll: int
    article_count: int = 0
    rss_url: str = ""


class SubscriptionListResponse(BaseModel):
    success: bool
    data: list = []


class PollerStatusResponse(BaseModel):
    success: bool
    data: dict = {}


# ── 订阅管理 ─────────────────────────────────────────────

@router.post("/rss/subscribe", response_model=SubscribeResponse, summary="添加 RSS 订阅")
async def subscribe(req: SubscribeRequest, request: Request):
    """
    添加一个公众号到 RSS 订阅列表。

    添加后，后台轮询器会定时拉取该公众号的最新文章。

    **请求体参数：**
    - **fakeid** (必填): 公众号 FakeID，通过搜索接口获取
    - **nickname** (可选): 公众号名称
    - **alias** (可选): 公众号微信号
    - **head_img** (可选): 公众号头像 URL
    """
    added = rss_store.add_subscription(
        fakeid=req.fakeid,
        nickname=req.nickname,
        alias=req.alias,
        head_img=req.head_img,
    )
    if added:
        logger.info("RSS subscription added: %s (%s)", req.nickname, req.fakeid[:8])
        return SubscribeResponse(success=True, message="订阅成功")
    return SubscribeResponse(success=True, message="已订阅，无需重复添加")


@router.delete("/rss/subscribe/{fakeid}", response_model=SubscribeResponse,
               summary="取消 RSS 订阅")
async def unsubscribe(fakeid: str):
    """
    取消订阅一个公众号，同时删除该公众号的缓存文章。

    **路径参数：**
    - **fakeid**: 公众号 FakeID
    """
    removed = rss_store.remove_subscription(fakeid)
    if removed:
        logger.info("RSS subscription removed: %s", fakeid[:8])
        return SubscribeResponse(success=True, message="已取消订阅")
    return SubscribeResponse(success=False, message="未找到该订阅")


@router.get("/rss/subscriptions", response_model=SubscriptionListResponse,
            summary="获取订阅列表")
async def get_subscriptions(request: Request):
    """
    获取当前所有 RSS 订阅的公众号列表。

    返回每个订阅的基本信息、缓存文章数和 RSS 地址。
    """
    subs = rss_store.list_subscriptions()
    base_url = get_base_url(request)

    items = []
    for s in subs:
        # 将头像 URL 转换为代理链接
        head_img = proxy_image_url(s.get("head_img", ""), base_url)
        fakeid = s['fakeid']
        # 统计历史文章数量
        historical_count = rss_store.count_historical_articles(fakeid)
        items.append({
            **s,
            "head_img": head_img,
            "rss_url": f"{base_url}/api/rss/{fakeid}",
            "historical_rss_url": f"{base_url}/api/rss/{fakeid}/history" if historical_count > 0 else "",
            "historical_count": historical_count,
        })

    return SubscriptionListResponse(success=True, data=items)


@router.post("/rss/poll", response_model=PollerStatusResponse,
             summary="手动触发轮询")
async def trigger_poll():
    """
    手动触发一次轮询，立即拉取所有订阅公众号的最新文章。

    通常用于首次订阅后立即获取文章，无需等待下一个轮询周期。
    """
    if not rss_poller.is_running:
        return PollerStatusResponse(
            success=False,
            data={"message": "轮询器未启动"}
        )
    try:
        await rss_poller.poll_now()
        return PollerStatusResponse(
            success=True,
            data={"message": "轮询完成"}
        )
    except Exception as e:
        return PollerStatusResponse(
            success=False,
            data={"message": f"轮询出错: {str(e)}"}
        )


@router.get("/rss/status", response_model=PollerStatusResponse,
            summary="轮询器状态")
async def poller_status():
    """
    获取 RSS 轮询器运行状态。
    """
    subs = rss_store.list_subscriptions()
    return PollerStatusResponse(
        success=True,
        data={
            "running": rss_poller.is_running,
            "polling": rss_poller._polling,
            "poll_interval": rss_poller._get_poll_interval(),
            "scheduled_time": rss_poller._get_scheduled_time(),
            "subscription_count": len(subs),
            "last_poll_time": rss_poller._last_poll_time,
            "last_new_count": rss_poller._last_new_count,
            "last_poll_message": rss_poller._last_poll_message,
            "login_expired": rss_poller._login_expired,
        },
    )


@router.get("/rss/logs", summary="获取轮询器日志")
async def get_poller_logs(limit: int = Query(200, ge=1, le=500)):
    """
    获取 RSS 轮询器最近的日志条目。
    """
    return {
        "success": True,
        "data": rss_poller.get_logs(limit=limit),
    }


# ── 聚合 RSS ─────────────────────────────────────────────

@router.get("/rss/all", summary="聚合 RSS 订阅源",
            response_class=Response)
async def get_aggregated_rss_feed(
    request: Request,
    limit: int = Query(RSS_AGGREGATED_DEFAULT, ge=1, le=RSS_AGGREGATED_MAX, description="文章数量上限"),
):
    """
    获取所有订阅公众号的聚合 RSS 2.0 订阅源。

    将此地址添加到 RSS 阅读器，即可在一个订阅源中查看所有公众号文章。
    订阅增减后自动生效，无需更换链接。
    """
    subs = rss_store.list_subscriptions()
    nickname_map = {s["fakeid"]: s.get("nickname") or s["fakeid"] for s in subs}

    articles = rss_store.get_all_articles(limit=limit) if subs else []

    base_url = get_base_url(request)
    
    # [2026-05-08 优化] 使用流式生成降低内存占用
    return StreamingResponse(
        generate_aggregated_rss_stream(articles, nickname_map, base_url),
        media_type="application/rss+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=600"},
    )


# ── 导出 ─────────────────────────────────────────────────

@router.get("/rss/export", summary="导出订阅列表")
async def export_subscriptions(
    request: Request,
    format: str = Query("csv", regex="^(csv|opml)$", description="导出格式: csv 或 opml"),
):
    """
    导出当前订阅列表。

    - **csv**: 包含公众号名称、FakeID、RSS 地址、文章数、订阅时间
    - **opml**: 标准 OPML 格式，可直接导入 RSS 阅读器
    """
    subs = rss_store.list_subscriptions()
    base_url = get_base_url(request)

    if format == "opml":
        return _build_opml_response(subs, base_url)
    return _build_csv_response(subs, base_url)


def _build_csv_response(subs: list, base_url: str) -> Response:
    buf = io.StringIO()
    buf.write('\ufeff')
    writer = csv.writer(buf)
    writer.writerow(["Name", "FakeID", "RSS URL", "Articles", "Subscribed At"])
    for s in subs:
        rss_url = f"{base_url}/api/rss/{s['fakeid']}"
        sub_date = datetime.fromtimestamp(
            s.get("created_at", 0), tz=timezone.utc
        ).strftime("%Y-%m-%d")
        writer.writerow([
            s.get("nickname") or s["fakeid"],
            s["fakeid"],
            rss_url,
            s.get("article_count", 0),
            sub_date,
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="wechat_rss_subscriptions.csv"'},
    )


def _build_opml_response(subs: list, base_url: str) -> Response:
    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "WeChat RSS Subscriptions"
    ET.SubElement(head, "dateCreated").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )

    body = ET.SubElement(opml, "body")
    group = ET.SubElement(body, "outline", text="WeChat RSS", title="WeChat RSS")

    for s in subs:
        name = s.get("nickname") or s["fakeid"]
        rss_url = f"{base_url}/api/rss/{s['fakeid']}"
        ET.SubElement(group, "outline", **{
            "type": "rss",
            "text": name,
            "title": name,
            "xmlUrl": rss_url,
            "htmlUrl": "https://mp.weixin.qq.com",
            "description": f"{name} - WeChat RSS",
        })

    xml_str = ET.tostring(opml, encoding="unicode", xml_declaration=False)
    content = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str

    return Response(
        content=content,
        media_type="application/xml; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="wechat_rss_subscriptions.opml"'},
    )


# ── RSS XML 输出 ──────────────────────────────────────────

def _rfc822(ts: int) -> str:
    """Unix 时间戳 → RFC 822 日期字符串"""
    if not ts:
        return ""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


# ── 手动粘贴链接抓取 ───────────────────────────────────────

class FetchByUrlsRequest(BaseModel):
    """批量粘贴文章链接抓取"""
    urls: List[str] = Field(..., description="微信文章链接列表（每行一个）")
    fakeid: str = Field("", description="目标公众号 fakeid（可选，留空则自动从文章中提取）")
    nickname: str = Field("", description="公众号名称（新建订阅时用，可选）")


@router.post("/rss/fetch-by-urls", summary="批量粘贴链接抓取文章")
async def fetch_by_urls(req: FetchByUrlsRequest):
    """
    手动粘贴微信文章链接，批量抓取全文并入库。
    不依赖微信登录态，直接访问公开文章页面，风险极低。

    - 如果指定了 fakeid，文章归属到该公众号
    - 如果未指定 fakeid，从文章 HTML 中自动提取公众号信息
    """
    from utils.article_fetcher import fetch_articles_batch
    from utils.helpers import extract_article_info, parse_article_url, has_article_content
    from utils import auth_manager

    # 清洗 URL 列表
    raw_urls = req.urls
    clean_urls = []
    for u in raw_urls:
        u = u.strip()
        if not u:
            continue
        # 支持纯链接和带文字的行，提取其中的 mp.weixin.qq.com 链接
        if "mp.weixin.qq.com/s" in u:
            # 提取完整 URL（去掉多余的空格和换行）
            start = u.index("https://") if "https://" in u else u.index("http://")
            clean_urls.append(u[start:])
        elif "mp.weixin.qq.com" in u:
            clean_urls.append(u)

    if not clean_urls:
        return {"success": False, "saved": 0, "message": "未找到有效的微信文章链接（需包含 mp.weixin.qq.com）"}

    # 去重
    seen = set()
    urls = []
    for u in clean_urls:
        if u not in seen:
            seen.add(u)
            urls.append(u)

    logger.info("开始批量抓取 %d 篇文章（粘贴链接模式）", len(urls))

    # 获取凭证（可选，带凭证能抓到更多内容，但不带也能抓公开文章）
    creds = {}
    try:
        c = auth_manager.get_credentials()
        if c and c.get("token") and c.get("cookie"):
            creds = c
    except Exception:
        pass

    # 批量抓取 HTML
    results = await fetch_articles_batch(
        urls,
        max_concurrency=3,
        timeout=30,
        wechat_token=creds.get("token"),
        wechat_cookie=creds.get("cookie"),
    )

    # 解析 HTML 并组装 article dicts
    # 预加载已有订阅列表，用于按公众号名匹配真实 fakeid（保证和自动轮询数据一致）
    existing_subs = {}
    try:
        for s in rss_store.list_subscriptions():
            if s.get("nickname"):
                existing_subs[s["nickname"]] = s["fakeid"]
    except Exception:
        pass

    articles_by_fakeid = {}  # {fakeid: [article_dict, ...]}
    sub_info = {}            # {fakeid: {nickname, head_img}}
    failed = []

    for url in urls:
        html = results.get(url)
        if not html or not has_article_content(html):
            failed.append(url)
            logger.warning("抓取失败（无正文）: %s", url[:80])
            continue

        params = parse_article_url(url) or {}
        info = extract_article_info(html, params)
        author = info.get("author", "") or "未知公众号"

        # 确定 fakeid（兼容自动轮询，避免重复）：
        # 1. 用户手动指定 → 用指定的
        # 2. 已订阅的公众号名 → 用订阅表里的真实 fakeid（和自动轮询完全一致）
        # 3. 都没有 → 用公众号名作为 fakeid（后续可通过订阅表自动合并）
        fakeid = req.fakeid or ""
        if not fakeid and author in existing_subs:
            fakeid = existing_subs[author]
            logger.info("按名称匹配订阅: %s → fakeid=%s", author, fakeid[:12])
        if not fakeid:
            fakeid = info.get("__biz", "") or author

        if fakeid not in sub_info:
            sub_info[fakeid] = {"nickname": author, "head_img": ""}

        # 图片代理处理（和自动轮询保持一致，解决微信 mmbiz 防盗链）
        from utils.content_processor import process_article_content
        site_url = os.getenv("SITE_URL", "http://localhost:5000").rstrip("/")
        processed = process_article_content(html, proxy_base_url=site_url)

        article = {
            "aid": params.get("mid", ""),
            "title": info.get("title", ""),
            "link": url,
            "digest": (processed.get("plain_content", "") or "")[:500],
            "cover": (processed.get("images") or [""])[0] if processed.get("images") else "",
            "author": author,
            "content": processed.get("content", ""),
            "plain_content": processed.get("plain_content", ""),
            "publish_time": info.get("publish_time", 0),
        }
        articles_by_fakeid.setdefault(fakeid, []).append(article)

    # 确保订阅存在，然后保存文章
    total_saved = 0
    saved_details = []
    for fakeid, articles in articles_by_fakeid.items():
        info = sub_info.get(fakeid, {})
        # 创建/确保订阅存在（fakeid 可能是 __biz 或手动指定）
        try:
            rss_store.add_subscription(
                fakeid=fakeid,
                nickname=info.get("nickname", ""),
                head_img=info.get("head_img", ""),
            )
        except Exception as e:
            logger.warning("创建订阅失败 %s: %s", fakeid[:12], e)

        # source='poll'：和自动轮询完全一致，避免同一篇文章因 source 不同而重复
        saved = rss_store.save_articles(fakeid, articles, source="poll")
        total_saved += saved
        saved_details.append({
            "fakeid": fakeid,
            "nickname": info.get("nickname", ""),
            "saved": saved,
            "total": len(articles),
        })

    msg = f"抓取完成 | 共 {len(urls)} 篇 | 成功入库 {total_saved} 篇"
    if failed:
        msg += f" | 失败 {len(failed)} 篇"

    return {
        "success": total_saved > 0,
        "saved": total_saved,
        "total": len(urls),
        "failed_count": len(failed),
        "details": saved_details,
        "message": msg,
    }
