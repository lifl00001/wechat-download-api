#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
新闻搜索路由 — 搜索源 CRUD + 新闻条目浏览 + 定时任务控制
"""

import logging
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from utils import news_store
from utils.news_fetcher import news_searcher, run_single_source_search, fetch_baidu, fetch_tavily, fetch_aihot, fetch_single_item_content
from utils.settings_manager import settings_manager

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 模型 ─────────────────────────────────────────────

class CreateSourceRequest(BaseModel):
    name: str = Field(..., description="搜索源名称", min_length=1, max_length=200)
    query_baidu: str = Field("", description="百度搜索关键词")
    query_tavily: str = Field("", description="Tavily 搜索关键词")
    query_aihot: str = Field("", description="AI HOT 关键词（空=拉全部精选）")
    category_id: Optional[int] = Field(None, description="关联分类 ID")
    search_engines: str = Field('["baidu","tavily"]', description="启用的搜索引擎 JSON 数组")
    tavily_topic: str = Field("news", description="Tavily topic")
    tavily_days: int = Field(7, description="Tavily 时间范围（天）")
    baidu_recency: str = Field("week", description="百度时效过滤: week/month/semiyear/year")
    aihot_mode: str = Field("selected", description="AI HOT 模式: selected/all")
    max_results: int = Field(10, description="每个引擎最大结果数")


class UpdateSourceRequest(BaseModel):
    name: Optional[str] = None
    query_baidu: Optional[str] = None
    query_tavily: Optional[str] = None
    query_aihot: Optional[str] = None
    category_id: Optional[int] = None
    search_engines: Optional[str] = None
    tavily_topic: Optional[str] = None
    tavily_days: Optional[int] = None
    baidu_recency: Optional[str] = None
    aihot_mode: Optional[str] = None
    max_results: Optional[int] = None
    is_active: Optional[int] = None


class OneTimeSearchRequest(BaseModel):
    query: str = Field(..., description="搜索关键词", min_length=1)
    engines: List[str] = Field(["baidu", "tavily"], description="搜索引擎列表")
    category_id: Optional[int] = Field(None, description="关联分类 ID")
    max_results: int = Field(10, description="每引擎最大结果数")
    baidu_recency: str = Field("week", description="百度时效过滤")
    tavily_topic: str = Field("news", description="Tavily topic")
    tavily_days: int = Field(7, description="Tavily 时间范围（天）")
    aihot_mode: str = Field("selected", description="AI HOT 模式: selected/all")


# ── 搜索源 CRUD ──────────────────────────────────────────────

@router.post("/sources", summary="创建搜索源")
async def create_source(req: CreateSourceRequest):
    """创建新的新闻搜索源"""
    try:
        source_id = news_store.add_news_source(
            name=req.name,
            query_baidu=req.query_baidu,
            query_tavily=req.query_tavily,
            query_aihot=req.query_aihot,
            category_id=req.category_id,
            search_engines=req.search_engines,
            tavily_topic=req.tavily_topic,
            tavily_days=req.tavily_days,
            baidu_recency=req.baidu_recency,
            aihot_mode=req.aihot_mode,
            max_results=req.max_results,
        )
        return {"success": True, "id": source_id, "message": f"搜索源 '{req.name}' 创建成功"}
    except Exception as e:
        if "UNIQUE constraint" in str(e) or "Duplicate entry" in str(e):
            raise HTTPException(status_code=409, detail=f"搜索源 '{req.name}' 已存在")
        logger.error("Failed to create source: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sources", summary="列出所有搜索源")
async def list_sources():
    """获取所有新闻搜索源列表"""
    sources = news_store.list_news_sources()
    return {"sources": sources, "total": len(sources)}


@router.patch("/sources/{source_id}", summary="更新搜索源")
async def update_source(source_id: int, req: UpdateSourceRequest):
    """更新搜索源配置"""
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    ok = news_store.update_news_source(source_id, **data)
    if not ok:
        raise HTTPException(status_code=404, detail="搜索源不存在")
    return {"success": True, "message": "更新成功"}


@router.delete("/sources/{source_id}", summary="删除搜索源")
async def delete_source(source_id: int):
    """删除搜索源及其所有新闻条目"""
    ok = news_store.remove_news_source(source_id)
    if not ok:
        raise HTTPException(status_code=404, detail="搜索源不存在")
    return {"success": True, "message": "删除成功"}


# ── 手动搜索 ─────────────────────────────────────────────────

@router.post("/sources/{source_id}/search", summary="手动触发单源搜索")
async def search_single_source(source_id: int):
    """手动触发指定搜索源的一次搜索"""
    source = news_store.get_news_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="搜索源不存在")

    # 检查 API Key 配置
    baidu_key = settings_manager.get("baidu_api_key")
    tavily_key = settings_manager.get("tavily_api_key")
    import json as _json
    engines = _json.loads(source.get("search_engines", '["baidu","tavily"]'))
    missing_keys = []
    if "baidu" in engines and not baidu_key:
        missing_keys.append("百度")
    if "tavily" in engines and not tavily_key:
        missing_keys.append("Tavily")
    if missing_keys:
        return {
            "success": False,
            "source": source.get("name", ""),
            "saved": 0,
            "message": f"未配置 {'、'.join(missing_keys)} API Key，请在「定时任务」标签页中配置",
        }

    try:
        saved = await _run_source_search_async(source_id)
        return {
            "success": True,
            "source": source.get("name", ""),
            "saved": saved,
            "message": f"搜索完成，新增 {saved} 条新闻",
        }
    except Exception as e:
        logger.error("Manual search error for source %d: %s", source_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search", summary="一次性搜索（不存 source）")
async def one_time_search(req: OneTimeSearchRequest):
    """
    临时搜索，结果不入库。
    用于快速测试搜索效果。
    """
    results = []
    for engine in req.engines:
        if engine == "baidu":
            items = fetch_baidu(req.query, recency=req.baidu_recency, max_results=req.max_results)
            for item in items:
                item["source_engine"] = "baidu"
            results.extend(items)
        elif engine == "tavily":
            items = fetch_tavily(req.query, topic=req.tavily_topic,
                                 days=req.tavily_days, max_results=req.max_results)
            for item in items:
                item["source_engine"] = "tavily"
            results.extend(items)
        elif engine == "aihot":
            items = fetch_aihot(query=req.query, mode=req.aihot_mode, max_results=req.max_results)
            for item in items:
                item["source_engine"] = "aihot"
            results.extend(items)

    return {
        "query": req.query,
        "total": len(results),
        "results": results,
    }


# ── 新闻条目 ─────────────────────────────────────────────────

@router.get("/items", summary="分页查询新闻")
async def list_items(
    category_id: Optional[int] = Query(None, description="分类 ID"),
    date_from: Optional[str] = Query(None, description="起始日期 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    engine: Optional[str] = Query(None, description="搜索引擎过滤"),
    source_id: Optional[int] = Query(None, description="搜索源 ID"),
    keyword: Optional[str] = Query(None, description="关键词搜索"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
):
    """分页查询新闻条目"""
    items = news_store.get_news_items(
        category_id=category_id,
        date_from=date_from,
        date_to=date_to,
        engine=engine,
        source_id=source_id,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    total = news_store.count_news_items(
        category_id=category_id,
        date_from=date_from,
        date_to=date_to,
        engine=engine,
        source_id=source_id,
        keyword=keyword,
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
    }


@router.get("/items/{item_id}", summary="新闻详情")
async def get_item(item_id: int):
    """获取单条新闻详情"""
    item = news_store.get_news_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="新闻不存在")
    return item


@router.post("/items/{item_id}/fetch-content", summary="抓取新闻全文")
async def fetch_item_content(item_id: int):
    """抓取单条新闻的原文全文并更新数据库"""
    item = news_store.get_news_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="新闻不存在")

    url = item.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="该新闻无原文链接")

    try:
        content = await _fetch_content_async(item_id)
        if content:
            return {"success": True, "content_length": len(content), "message": f"全文抓取成功（{len(content)} 字）"}
        else:
            raise HTTPException(status_code=500, detail="全文抓取失败，可能该页面无法访问或内容无法提取")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Fetch content error for item %d: %s", item_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 日报数据 ─────────────────────────────────────────────────

@router.get("/daily-report", summary="日报数据")
async def daily_report(
    category_id: Optional[int] = Query(None, description="分类 ID"),
    date: Optional[str] = Query(None, description="日期 YYYY-MM-DD"),
):
    """按分类汇总的日报数据（跨引擎去重）"""
    items = news_store.get_daily_report_data(category_id=category_id, date=date)
    return {
        "date": date,
        "category_id": category_id,
        "total": len(items),
        "items": items,
    }


# ── 搜索器状态 ──────────────────────────────────────────────

@router.get("/status", summary="搜索器状态")
async def get_status():
    """获取搜索器运行状态"""
    return news_searcher.get_status()


@router.post("/trigger", summary="手动触发全部搜索")
async def trigger_all():
    """手动触发所有活跃搜索源的一次搜索"""
    try:
        source_count, item_count = await news_searcher.search_now()
        return {
            "success": True,
            "sources": source_count,
            "saved": item_count,
            "message": f"搜索完成：{source_count} 个源，新增 {item_count} 条新闻",
        }
    except Exception as e:
        logger.error("Trigger all search error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 辅助函数 ─────────────────────────────────────────────────

async def _run_source_search_async(source_id: int) -> int:
    """在线程池中异步执行单源搜索"""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, run_single_source_search, source_id)


async def _fetch_content_async(item_id: int) -> Optional[str]:
    """在线程池中异步执行全文抓取"""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fetch_single_item_content, item_id)
