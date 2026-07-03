#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
投资分类同步模块。

从 ruoyi-vue-pro.stock_investment_category 表读取投资分类，
提取 prompt_template 中的【关键词】段落，同步为新闻搜索源（news_sources）。
百度 + 豆包共用 query_baidu 关键词。

设计要点：
- 跨库读取：复用 .env 的 DB_HOST/DB_USERNAME/DB_PASSWORD，仅切换 database 为 ruoyi-vue-pro
- 幂等同步：搜索源以 name 为唯一键，已存在则更新关键词，不存在则新建
- 关键词提取：从 prompt_template 的【关键词】xxx、xxx、xxx。 段落提取，顿号分隔
- 回退策略：提取失败时用分类 name 作为关键词
"""

import os
import re
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ruoyi-vue-pro 库名（与 we_mp 同一台 MySQL 服务器，仅库名不同）
_RUOYI_DB = os.getenv("STOCK_CATEGORY_DB", "ruoyi-vue-pro")

# 【关键词】段落提取：匹配 【关键词】 到下一个【或字符串结尾
_KW_PATTERN = re.compile(r'【关键词】(.+?)(?=【|$)', re.S)
# 关键词切分：顿号 / 中英文逗号 / 空格 / 换行
_KW_SPLIT = re.compile(r'[、,，\s\n]+')


def _connect_ruoyi():
    """连接 ruoyi-vue-pro 库（独立短连接，仅同步时使用，不复用连接池）"""
    import pymysql
    from dotenv import load_dotenv
    load_dotenv()
    host = os.getenv("DB_HOST", "").strip()
    port = int(os.getenv("DB_PORT", "3306") or "3306")
    user = os.getenv("DB_USERNAME", "").strip()
    pwd = os.getenv("DB_PASSWORD", "").strip()
    if not (host and user):
        raise RuntimeError("DB_HOST / DB_USERNAME 未配置，无法连接 ruoyi-vue-pro")
    return pymysql.connect(
        host=host, port=port, user=user, password=pwd,
        database=_RUOYI_DB, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def extract_keywords(prompt_template: str, fallback: str = "") -> str:
    """
    从 prompt_template 提取【关键词】段落，返回空格拼接的关键词字符串。
    提取失败时返回 fallback（通常是分类名）。
    """
    if not prompt_template:
        return fallback
    m = _KW_PATTERN.search(prompt_template)
    if not m:
        return fallback
    raw = m.group(1).strip().rstrip("。.")
    kws = [k.strip() for k in _KW_SPLIT.split(raw) if k.strip()]
    # 过滤掉过短的碎片（如单个字母 "AI" 被切散为 "A","I"）——保留长度>=2 或纯英文>=1
    kws = [k for k in kws if len(k) >= 2 or (k.isascii() and k.isalpha())]
    if not kws:
        return fallback
    return " ".join(kws)


def fetch_stock_categories() -> List[Dict]:
    """
    从 stock_investment_category 读取所有未删除的活跃分类。
    返回列表，每项含 id/name/code/parent_id/level/keywords。
    """
    conn = _connect_ruoyi()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, code, parent_id, level, prompt_template
                FROM stock_investment_category
                WHERE deleted = 0 AND status = 1
                ORDER BY parent_id, sort
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        kws = extract_keywords(r.get("prompt_template", ""), fallback=r["name"])
        result.append({
            "stock_id": r["id"],
            "name": r["name"],
            "code": r["code"],
            "parent_id": r["parent_id"],
            "level": r["level"],
            "keywords": kws,
        })
    logger.info("从 stock_investment_category 读取 %d 个活跃分类", len(result))
    return result


def _get_parent_label(cat: Dict, all_cats: List[Dict]) -> str:
    """获取父分类名，用于子分类搜索源命名（如「人工智能/大模型」）"""
    if cat["parent_id"] == 0:
        return ""
    for p in all_cats:
        if p["stock_id"] == cat["parent_id"]:
            return p["name"]
    return ""


def sync_stock_categories_to_sources(
    engines: str = '["baidu","doubao","tavily"]',
    max_results: int = 10,
) -> Tuple[int, int, List[str]]:
    """
    同步 stock_investment_category 到 news_sources。

    策略：
    - 搜索源 name 用分类名（子分类带父前缀避免歧义，如「半导体/存储芯片」）
    - 关键词同时填入 query_baidu（百度+豆包）和 query_tavily（Tavily）
    - 已存在同名源 → 更新 query_baidu 和 query_tavily（保留用户改过的引擎/分类等其他字段）
    - 不存在 → 新建
    - 分类表中删除的分类不会自动删搜索源（避免误删用户数据）

    Args:
        engines: 新建搜索源的默认引擎 JSON
        max_results: 新建搜索源的默认最大结果数

    Returns:
        (created, updated, messages)
    """
    from utils import news_store

    cats = fetch_stock_categories()
    if not cats:
        return 0, 0, ["未读取到任何投资分类"]

    existing = {s["name"]: s for s in news_store.list_news_sources()}
    created, updated = 0, 0
    messages = []

    for cat in cats:
        # 子分类带父分类前缀，一级分类直接用名
        parent = _get_parent_label(cat, cats)
        source_name = f"{parent}/{cat['name']}" if parent and cat["level"] > 1 else cat["name"]
        keywords = cat["keywords"] or cat["name"]

        if source_name in existing:
            # 已存在：更新关键词（query_baidu + query_tavily）+ 补全缺失的引擎
            src = existing[source_name]
            old_kw = src.get("query_baidu", "")
            old_kw_t = src.get("query_tavily", "")
            kw_changed = (old_kw != keywords or old_kw_t != keywords)

            # 合并目标引擎到现有引擎（保留用户额外配置的引擎，如 aihot）
            import json as _json
            try:
                cur_engines = _json.loads(src.get("search_engines", "[]"))
            except Exception:
                cur_engines = []
            try:
                tgt_engines = _json.loads(engines)
            except Exception:
                tgt_engines = []
            merged = list(cur_engines)
            for e in tgt_engines:
                if e not in merged:
                    merged.append(e)
            eng_changed = (merged != cur_engines)

            updates = {}
            if kw_changed:
                updates["query_baidu"] = keywords
                updates["query_tavily"] = keywords
            if eng_changed:
                updates["search_engines"] = _json.dumps(merged, ensure_ascii=False)
            if updates:
                news_store.update_news_source(src["id"], **updates)
                updated += 1
                tags = []
                if kw_changed:
                    tags.append(f"关键词{len(keywords)}字")
                if eng_changed:
                    tags.append(f"引擎→{_json.dumps(merged, ensure_ascii=False)}")
                messages.append(f"更新「{source_name}」({', '.join(tags)})")
            else:
                messages.append(f"跳过「{source_name}」(无变化)")
        else:
            # 新建
            news_store.add_news_source(
                name=source_name,
                query_baidu=keywords,
                query_tavily=keywords,
                search_engines=engines,
                max_results=max_results,
            )
            created += 1
            messages.append(f"新建「{source_name}」")

    logger.info("投资分类同步完成: 新建 %d, 更新 %d", created, updated)
    return created, updated, messages
