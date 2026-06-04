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
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor

import requests

from utils import news_store
from utils.settings_manager import settings_manager

logger = logging.getLogger(__name__)

# HTTP 超时配置
_BAIDU_TIMEOUT = 30
_TAVILY_TIMEOUT = 60
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


# ── 搜索循环 ─────────────────────────────────────────────────

def run_search_cycle():
    """
    遍历所有活跃搜索源，调用配置的搜索引擎，保存结果
    返回 (总源数, 总新增条目数)
    """
    sources = news_store.get_active_sources()
    if not sources:
        logger.info("No active news sources, skipping search cycle")
        return 0, 0

    total_saved = 0
    for source in sources:
        source_id = source["id"]
        name = source.get("name", "")
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

        if "tavily" in engines:
            query = source.get("query_tavily", "")
            if query:
                topic = source.get("tavily_topic", "news")
                days = source.get("tavily_days", 7)
                items = fetch_tavily(query, topic=topic, days=days, max_results=max_results)
                if items:
                    saved += news_store.save_news_items(source_id, items, "tavily")

        total_saved += saved
        logger.info("Source '%s' (id=%d): saved %d items", name, source_id, saved)

    logger.info("Search cycle complete: %d sources, %d total items saved", len(sources), total_saved)
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

    if "tavily" in engines:
        query = source.get("query_tavily", "")
        if query:
            topic = source.get("tavily_topic", "news")
            days = source.get("tavily_days", 7)
            items = fetch_tavily(query, topic=topic, days=days, max_results=max_results)
            if items:
                saved += news_store.save_news_items(source_id, items, "tavily")

    return saved


# ── 后台定时循环（单例）─────────────────────────────────────────

class NewsSearcher:
    """新闻搜索定时循环单例"""

    _instance = None
    _task: Optional[asyncio.Task] = None
    _running = False
    _last_search_time: int = 0
    _last_search_results: str = ""

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("News searcher started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("News searcher stopped")

    async def _loop(self):
        """后台循环：按间隔执行搜索"""
        while self._running:
            try:
                interval = settings_manager.get_int("news_search_interval", 21600)
                interval = max(interval, 60)
                logger.info("News searcher sleeping %ds until next cycle", interval)
                await asyncio.sleep(interval)

                if not self._running:
                    break

                loop = asyncio.get_event_loop()
                source_count, item_count = await loop.run_in_executor(None, run_search_cycle)
                self._last_search_time = int(time.time())
                self._last_search_results = f"{source_count} sources, {item_count} items"

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("News search cycle error: %s", e)
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    break

    async def search_now(self):
        """手动触发一次搜索"""
        loop = asyncio.get_event_loop()
        source_count, item_count = await loop.run_in_executor(None, run_search_cycle)
        self._last_search_time = int(time.time())
        self._last_search_results = f"{source_count} sources, {item_count} items"
        return source_count, item_count

    def get_status(self) -> Dict:
        """获取搜索器状态"""
        interval = settings_manager.get_int("news_search_interval", 21600)
        db_status = news_store.get_search_status()
        return {
            "running": self._running,
            "interval_seconds": interval,
            "last_search_at": self._last_search_time,
            "last_search_results": self._last_search_results,
            **db_status,
        }


# 全局单例
news_searcher = NewsSearcher()
