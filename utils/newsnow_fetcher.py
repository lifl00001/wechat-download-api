#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
newsnow 数据源采集器
从本地部署的 newsnow 实例抓取多平台热榜（微博/知乎/B站/头条/抖音等30+平台）

newsnow 本身只缓存当前快照（TTL约10分钟），不存历史数据。
本模块负责把 newsnow 的快照持久化到 we_mp 数据库的 news_items 表。

用法：
    from utils.newsnow_fetcher import fetch_newsnow
    items = fetch_newsnow()  # 抓取所有平台热榜
    items = fetch_newsnow(sources=["weibo","zhihu"])  # 指定平台

前提：newsnow 本地实例已启动（cd ~/newsnow && pnpm dev，端口5173）
"""

import logging
import os
from typing import List, Dict

import requests

logger = logging.getLogger(__name__)

# 从环境变量读 newsnow 地址，默认本地5173
_NEWSNOW_BASE = os.getenv("NEWSNOW_BASE_URL", "http://localhost:5173").rstrip("/")
_NEWSNOW_TIMEOUT = 15

# 默认抓取的平台列表（已验证可用的10个）
DEFAULT_SOURCES = [
    "weibo",       # 微博热搜
    "zhihu",       # 知乎热榜
    "bilibili",    # B站热门
    "toutiao",     # 今日头条
    "douyin",      # 抖音热榜
    "ithome",      # IT之家
    "sspai",       # 少数派
    "juejin",      # 掘金
    "36kr",        # 36氪
    "solidot",     # Solidot
]


def fetch_newsnow(sources: List[str] = None, max_per_source: int = 30) -> List[Dict]:
    """
    从本地 newsnow 实例抓取多平台热榜，返回标准化的新闻列表

    Args:
        sources: 要抓取的平台ID列表，None则用 DEFAULT_SOURCES
        max_per_source: 每个平台最多取多少条

    Returns:
        标准化的新闻条目列表，每条含：
        - title: 标题
        - url: 链接
        - snippet: 摘要（用 description 或 hot 值）
        - full_text: 全文（热榜无全文，留空）
        - source_engine: 来源引擎标记（如 newsnow_weibo）
        - source_name: 来源显示名（如 newsnow-weibo）
        - published_date: 发布日期（热榜通常无精确日期，留空）
        - author: 作者（用平台名）
        - score: 相关度评分（用 hot 值或0）
    """
    if sources is None:
        sources = DEFAULT_SOURCES

    all_items = []

    for source_id in sources:
        try:
            resp = requests.get(
                f"{_NEWSNOW_BASE}/api/s",
                params={"id": source_id},
                timeout=_NEWSNOW_TIMEOUT,
            )

            if resp.status_code != 200:
                logger.warning("newsnow %s returned %d", source_id, resp.status_code)
                continue

            data = resp.json()
            # newsnow API 返回格式：{ status, id, updatedTime, items: [...] }
            raw_items = data.get("items", data.get("data", {}).get("items", []))

            for item in raw_items[:max_per_source]:
                title = item.get("title", "") or item.get("name", "")
                if not title:
                    continue

                url = item.get("url", "") or item.get("mobileUrl", "")
                # hot 值可能是数字或字符串（如 "123万"）
                hot = item.get("hot", "") or item.get("extra", "")
                snippet = item.get("description", "") or (f"热度: {hot}" if hot else "")

                all_items.append({
                    "title": title,
                    "url": url,
                    "snippet": str(snippet)[:500],
                    "full_text": "",  # 热榜无全文，需要时可后续用 fetch_article_content 抓取
                    "source_engine": f"newsnow_{source_id}",
                    "source_name": f"newsnow-{source_id}",
                    "published_date": "",
                    "author": source_id,  # 用平台ID作为作者标记
                    "score": _parse_hot(hot),
                })

            logger.info("newsnow %s: %d items", source_id, len(raw_items))

        except requests.exceptions.ConnectionError:
            logger.warning("newsnow 实例未启动（%s），跳过。请运行: cd ~/newsnow && pnpm dev", _NEWSNOW_BASE)
            break  # 实例没启动，后面的平台也不会成功，直接跳出
        except requests.exceptions.Timeout:
            logger.warning("newsnow %s timeout", source_id)
        except Exception as e:
            logger.error("newsnow %s error: %s", source_id, e)

    logger.info("newsnow total: %d items from %d sources", len(all_items), len(sources))
    return all_items


def _parse_hot(hot_value) -> float:
    """把热榜的热度值解析为数字评分（用于排序）"""
    if not hot_value:
        return 0.0
    try:
        s = str(hot_value).strip()
        # 处理 "123万" 这种中文单位
        if "万" in s:
            return float(s.replace("万", "")) * 10000
        if "亿" in s:
            return float(s.replace("亿", "")) * 100000000
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def get_available_sources() -> List[str]:
    """获取 newsnow 实际可用的平台列表（从 newsnow 的 /api/config 或硬编码）"""
    try:
        resp = requests.get(f"{_NEWSNOW_BASE}/api/config", timeout=5)
        # 如果有配置接口，动态获取
        data = resp.json()
        if isinstance(data, dict) and "sources" in data:
            return list(data["sources"].keys())
    except Exception:
        pass
    # fallback 到默认列表
    return DEFAULT_SOURCES
