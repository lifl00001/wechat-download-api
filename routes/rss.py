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
from typing import Optional
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
            "poll_interval": POLL_INTERVAL,
            "subscription_count": len(subs),
        },
    )


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
