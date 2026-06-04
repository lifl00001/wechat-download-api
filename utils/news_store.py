#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
新闻数据存储 — SQLite / MySQL
管理新闻搜索源配置和新闻条目
"""

import time
import hashlib
import logging
from typing import List, Dict, Optional
from utils import db_manager

logger = logging.getLogger(__name__)


def _safe_create_index(conn, index_name: str, table_name: str, columns: str):
    """安全创建索引（兼容 SQLite 和 MySQL）"""
    if db_manager.USE_MYSQL:
        try:
            db_manager.execute(conn,
                f"ALTER TABLE {table_name} ADD INDEX {index_name} ({columns})")
        except Exception:
            pass  # 索引已存在，忽略
    else:
        db_manager.execute(conn,
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name}({columns})")
    db_manager.commit(conn)


def _safe_add_column(conn, table: str, column: str, definition: str):
    """安全添加列（幂等，已存在则忽略）"""
    try:
        if db_manager.USE_MYSQL:
            db_manager.execute(conn, f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        else:
            db_manager.execute(conn, f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        db_manager.commit(conn)
    except Exception:
        pass  # 列已存在，忽略


def init_news_db():
    """建表（幂等，兼容 SQLite 和 MySQL）"""
    conn = db_manager.get_conn()
    try:
        if db_manager.USE_MYSQL:
            db_manager.adapt_executescript(conn, """
                CREATE TABLE IF NOT EXISTS news_sources (
                    id              INT AUTO_INCREMENT PRIMARY KEY,
                    name            VARCHAR(200) NOT NULL UNIQUE,
                    query_baidu     TEXT NOT NULL,
                    query_tavily    TEXT NOT NULL,
                    query_aihot     VARCHAR(2000) NOT NULL DEFAULT '',
                    category_id     INT DEFAULT NULL,
                    search_engines  VARCHAR(200) NOT NULL DEFAULT '["baidu","tavily"]',
                    tavily_topic    VARCHAR(50) NOT NULL DEFAULT 'news',
                    tavily_days     INT NOT NULL DEFAULT 7,
                    baidu_recency   VARCHAR(50) NOT NULL DEFAULT 'week',
                    max_results     INT NOT NULL DEFAULT 10,
                    aihot_mode      VARCHAR(20) NOT NULL DEFAULT 'selected',
                    is_active       INT NOT NULL DEFAULT 1,
                    last_search_at  INT NOT NULL DEFAULT 0,
                    created_at      INT NOT NULL,
                    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS news_items (
                    id              INT AUTO_INCREMENT PRIMARY KEY,
                    source_id       INT NOT NULL,
                    title           VARCHAR(500) NOT NULL DEFAULT '',
                    url             TEXT NOT NULL,
                    snippet         TEXT NOT NULL,
                    full_text       MEDIUMTEXT NOT NULL,
                    source_engine   VARCHAR(50) NOT NULL DEFAULT '',
                    source_name     VARCHAR(200) NOT NULL DEFAULT '',
                    author          VARCHAR(200) NOT NULL DEFAULT '',
                    published_date  VARCHAR(100) NOT NULL DEFAULT '',
                    published_ts    INT NOT NULL DEFAULT 0,
                    relevance_score DOUBLE NOT NULL DEFAULT 0,
                    category_id     INT DEFAULT NULL,
                    url_hash        VARCHAR(64) NOT NULL DEFAULT '',
                    fetched_at      INT NOT NULL,
                    UNIQUE KEY uq_url_engine (url_hash, source_engine),
                    FOREIGN KEY (source_id) REFERENCES news_sources(id) ON DELETE CASCADE,
                    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
                );
            """)
        else:
            db_manager.adapt_executescript(conn, """
                CREATE TABLE IF NOT EXISTS news_sources (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT NOT NULL UNIQUE,
                    query_baidu     TEXT NOT NULL DEFAULT '',
                    query_tavily    TEXT NOT NULL DEFAULT '',
                    query_aihot     TEXT NOT NULL DEFAULT '',
                    category_id     INTEGER DEFAULT NULL,
                    search_engines  TEXT NOT NULL DEFAULT '["baidu","tavily"]',
                    tavily_topic    TEXT NOT NULL DEFAULT 'news',
                    tavily_days     INTEGER NOT NULL DEFAULT 7,
                    baidu_recency   TEXT NOT NULL DEFAULT 'week',
                    max_results     INTEGER NOT NULL DEFAULT 10,
                    aihot_mode      TEXT NOT NULL DEFAULT 'selected',
                    is_active       INTEGER NOT NULL DEFAULT 1,
                    last_search_at  INTEGER NOT NULL DEFAULT 0,
                    created_at      INTEGER NOT NULL,
                    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS news_items (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id       INTEGER NOT NULL,
                    title           TEXT NOT NULL DEFAULT '',
                    url             TEXT NOT NULL DEFAULT '',
                    snippet         TEXT NOT NULL DEFAULT '',
                    full_text       TEXT NOT NULL DEFAULT '',
                    source_engine   TEXT NOT NULL DEFAULT '',
                    source_name     TEXT NOT NULL DEFAULT '',
                    author          TEXT NOT NULL DEFAULT '',
                    published_date  TEXT NOT NULL DEFAULT '',
                    published_ts    INTEGER NOT NULL DEFAULT 0,
                    relevance_score REAL NOT NULL DEFAULT 0,
                    category_id     INTEGER DEFAULT NULL,
                    url_hash        TEXT NOT NULL DEFAULT '',
                    fetched_at      INTEGER NOT NULL,
                    UNIQUE(url_hash, source_engine),
                    FOREIGN KEY (source_id) REFERENCES news_sources(id) ON DELETE CASCADE,
                    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
                );
            """)
        db_manager.commit(conn)

        # 创建索引
        _safe_create_index(conn, "idx_news_items_source", "news_items", "source_id, published_ts DESC")
        _safe_create_index(conn, "idx_news_items_category_date", "news_items", "category_id, published_ts DESC")
        _safe_create_index(conn, "idx_news_sources_category", "news_sources", "category_id")

        # 增量添加 AI HOT 字段（幂等，已存在则忽略）
        if db_manager.USE_MYSQL:
            _safe_add_column(conn, "news_sources", "query_aihot", "VARCHAR(2000) NOT NULL DEFAULT ''")
        else:
            _safe_add_column(conn, "news_sources", "query_aihot", "TEXT NOT NULL DEFAULT ''")
        _safe_add_column(conn, "news_sources", "aihot_mode", "VARCHAR(20) NOT NULL DEFAULT 'selected'")

        logger.info("News tables initialized")
    finally:
        conn.close()


def normalize_url(url: str) -> str:
    """URL 标准化 → SHA256 hash"""
    if not url:
        return ""
    # 去除首尾空白、fragment、trailing slash
    url = url.strip()
    if "#" in url:
        url = url[:url.index("#")]
    url = url.rstrip("/")
    # 小写化域名部分
    if "://" in url:
        proto, rest = url.split("://", 1)
        if "/" in rest:
            domain, path = rest.split("/", 1)
            url = proto + "://" + domain.lower() + "/" + path
        else:
            url = proto + "://" + rest.lower()
    return hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()


# ── 搜索源 CRUD ──────────────────────────────────────────────

def add_news_source(name: str, query_baidu: str = "", query_tavily: str = "",
                    category_id: Optional[int] = None,
                    search_engines: str = '["baidu","tavily"]',
                    tavily_topic: str = "news", tavily_days: int = 7,
                    baidu_recency: str = "week", max_results: int = 10,
                    query_aihot: str = "", aihot_mode: str = "selected") -> int:
    """创建搜索源，返回新 ID"""
    conn = db_manager.get_conn()
    try:
        now = int(time.time())
        cursor = db_manager.execute(conn, """
            INSERT INTO news_sources
                (name, query_baidu, query_tavily, category_id, search_engines,
                 tavily_topic, tavily_days, baidu_recency, max_results,
                 query_aihot, aihot_mode,
                 is_active, last_search_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
        """, (name, query_baidu, query_tavily, category_id, search_engines,
              tavily_topic, tavily_days, baidu_recency, max_results,
              query_aihot, aihot_mode, now))
        db_manager.commit(conn)
        new_id = db_manager.lastrowid(cursor)
        cursor.close()
        logger.info("Added news source: %s (id=%d)", name, new_id)
        return new_id
    finally:
        conn.close()


def remove_news_source(source_id: int) -> bool:
    """删除搜索源及其新闻"""
    conn = db_manager.get_conn()
    try:
        cursor = db_manager.execute(conn, "DELETE FROM news_sources WHERE id = ?", (source_id,))
        db_manager.commit(conn)
        affected = cursor.rowcount
        cursor.close()
        return affected > 0
    finally:
        conn.close()


def update_news_source(source_id: int, **kwargs) -> bool:
    """更新搜索源字段"""
    if not kwargs:
        return False
    allowed = {"name", "query_baidu", "query_tavily", "category_id",
               "search_engines", "tavily_topic", "tavily_days",
               "baidu_recency", "max_results", "is_active", "last_search_at",
               "query_aihot", "aihot_mode"}
    fields = []
    values = []
    for k, v in kwargs.items():
        if k in allowed:
            fields.append(f"{k} = ?")
            values.append(v)
    if not fields:
        return False
    values.append(source_id)
    conn = db_manager.get_conn()
    try:
        sql = "UPDATE news_sources SET " + ", ".join(fields) + " WHERE id = ?"
        cursor = db_manager.execute(conn, sql, tuple(values))
        db_manager.commit(conn)
        affected = cursor.rowcount
        cursor.close()
        return affected > 0
    finally:
        conn.close()


def list_news_sources() -> List[Dict]:
    """列出所有搜索源（含分类名、条目数）"""
    conn = db_manager.get_conn()
    try:
        rows = db_manager.fetchall(conn, """
            SELECT ns.*,
                   c.name AS category_name,
                   (SELECT COUNT(*) FROM news_items WHERE source_id = ns.id) AS item_count
            FROM news_sources ns
            LEFT JOIN categories c ON ns.category_id = c.id
            ORDER BY ns.created_at DESC
        """)
        return rows
    finally:
        conn.close()


def get_news_source(source_id: int) -> Optional[Dict]:
    """获取单个搜索源"""
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchone(conn,
            "SELECT ns.*, c.name AS category_name FROM news_sources ns LEFT JOIN categories c ON ns.category_id = c.id WHERE ns.id = ?",
            (source_id,))
    finally:
        conn.close()


def get_active_sources() -> List[Dict]:
    """获取所有活跃的搜索源"""
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchall(conn,
            "SELECT * FROM news_sources WHERE is_active = 1 ORDER BY id")
    finally:
        conn.close()


# ── 新闻条目 ─────────────────────────────────────────────────

def save_news_items(source_id: int, items: List[Dict], engine: str) -> int:
    """
    批量保存新闻条目（url_hash + source_engine 去重）
    返回实际新增数量
    """
    if not items:
        return 0
    conn = db_manager.get_conn()
    try:
        now = int(time.time())
        saved = 0
        # 先取 source 的 category_id
        source = db_manager.fetchone(conn,
            "SELECT category_id, name FROM news_sources WHERE id = ?", (source_id,))
        if not source:
            return 0
        category_id = source.get("category_id")
        source_name = source.get("name", "")

        for item in items:
            url = item.get("url", "")
            url_hash = normalize_url(url)
            if not url_hash:
                continue

            title = item.get("title", "")
            snippet = item.get("snippet", item.get("content", ""))
            full_text = item.get("full_text", item.get("raw_content", ""))
            author = item.get("author", "")
            published_date = item.get("published_date", item.get("date", ""))
            relevance_score = float(item.get("score", 0) or 0)

            # 解析 published_ts
            published_ts = item.get("published_ts", 0)
            if not published_ts and published_date:
                try:
                    from datetime import datetime
                    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                        try:
                            dt = datetime.strptime(published_date[:19], fmt)
                            published_ts = int(dt.timestamp())
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass

            try:
                if db_manager.USE_MYSQL:
                    db_manager.execute(conn, """
                        INSERT INTO news_items
                            (source_id, title, url, snippet, full_text, source_engine,
                             source_name, author, published_date, published_ts,
                             relevance_score, category_id, url_hash, fetched_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            title = IF(LENGTH(VALUES(title)) > LENGTH(title), VALUES(title), title),
                            snippet = IF(LENGTH(VALUES(snippet)) > LENGTH(snippet), VALUES(snippet), snippet),
                            full_text = IF(LENGTH(VALUES(full_text)) > LENGTH(full_text), VALUES(full_text), full_text),
                            relevance_score = VALUES(relevance_score)
                    """, (source_id, title, url, snippet, full_text, engine,
                          source_name, author, published_date, published_ts,
                          relevance_score, category_id, url_hash, now))
                else:
                    db_manager.execute(conn, """
                        INSERT INTO news_items
                            (source_id, title, url, snippet, full_text, source_engine,
                             source_name, author, published_date, published_ts,
                             relevance_score, category_id, url_hash, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(url_hash, source_engine) DO UPDATE SET
                            title = CASE WHEN LENGTH(excluded.title) > LENGTH(title) THEN excluded.title ELSE title END,
                            snippet = CASE WHEN LENGTH(excluded.snippet) > LENGTH(snippet) THEN excluded.snippet ELSE snippet END,
                            full_text = CASE WHEN LENGTH(excluded.full_text) > LENGTH(full_text) THEN excluded.full_text ELSE full_text END,
                            relevance_score = excluded.relevance_score
                    """, (source_id, title, url, snippet, full_text, engine,
                          source_name, author, published_date, published_ts,
                          relevance_score, category_id, url_hash, now))
                saved += 1
            except db_manager.IntegrityError:
                pass
            except Exception as e:
                logger.warning("Failed to save news item %s: %s", url[:80], e)

        db_manager.commit(conn)

        # 更新 source 的 last_search_at
        db_manager.execute(conn,
            "UPDATE news_sources SET last_search_at = ? WHERE id = ?",
            (now, source_id))
        db_manager.commit(conn)

        logger.info("Saved %d/%d news items for source %d (%s)", saved, len(items), source_id, engine)
        return saved
    finally:
        conn.close()


def get_news_items(category_id: Optional[int] = None,
                   date_from: Optional[str] = None,
                   date_to: Optional[str] = None,
                   engine: Optional[str] = None,
                   source_id: Optional[int] = None,
                   keyword: Optional[str] = None,
                   page: int = 1,
                   page_size: int = 20) -> List[Dict]:
    """分页查询新闻条目"""
    conditions = []
    params = []

    if category_id is not None:
        conditions.append("ni.category_id = ?")
        params.append(category_id)
    if source_id is not None:
        conditions.append("ni.source_id = ?")
        params.append(source_id)
    if engine:
        conditions.append("ni.source_engine = ?")
        params.append(engine)
    if date_from:
        conditions.append("ni.published_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("ni.published_date <= ?")
        params.append(date_to + " 23:59:59")
    if keyword:
        conditions.append("(ni.title LIKE ? OR ni.snippet LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (page - 1) * page_size

    conn = db_manager.get_conn()
    try:
        rows = db_manager.fetchall(conn, f"""
            SELECT ni.*, ns.name AS source_name, c.name AS category_name
            FROM news_items ni
            LEFT JOIN news_sources ns ON ni.source_id = ns.id
            LEFT JOIN categories c ON ni.category_id = c.id
            {where}
            ORDER BY ni.published_ts DESC, ni.fetched_at DESC
            LIMIT ? OFFSET ?
        """, tuple(params + [page_size, offset]))
        return rows
    finally:
        conn.close()


def count_news_items(category_id: Optional[int] = None,
                     date_from: Optional[str] = None,
                     date_to: Optional[str] = None,
                     engine: Optional[str] = None,
                     source_id: Optional[int] = None,
                     keyword: Optional[str] = None) -> int:
    """统计新闻条目总数"""
    conditions = []
    params = []

    if category_id is not None:
        conditions.append("category_id = ?")
        params.append(category_id)
    if source_id is not None:
        conditions.append("source_id = ?")
        params.append(source_id)
    if engine:
        conditions.append("source_engine = ?")
        params.append(engine)
    if date_from:
        conditions.append("published_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("published_date <= ?")
        params.append(date_to + " 23:59:59")
    if keyword:
        conditions.append("(title LIKE ? OR snippet LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    conn = db_manager.get_conn()
    try:
        row = db_manager.fetchone(conn, f"SELECT COUNT(*) AS cnt FROM news_items {where}", tuple(params))
        return row.get("cnt", 0) if row else 0
    finally:
        conn.close()


def get_news_item(item_id: int) -> Optional[Dict]:
    """获取单条新闻详情"""
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchone(conn, """
            SELECT ni.*, ns.name AS source_name, c.name AS category_name
            FROM news_items ni
            LEFT JOIN news_sources ns ON ni.source_id = ns.id
            LEFT JOIN categories c ON ni.category_id = c.id
            WHERE ni.id = ?
        """, (item_id,))
    finally:
        conn.close()


def update_news_item_full_text(item_id: int, full_text: str) -> bool:
    """更新单条新闻的全文内容"""
    conn = db_manager.get_conn()
    try:
        cursor = db_manager.execute(conn,
            "UPDATE news_items SET full_text = ? WHERE id = ?",
            (full_text, item_id))
        db_manager.commit(conn)
        return cursor.rowcount > 0
    finally:
        conn.close()


# ── 日报数据 ─────────────────────────────────────────────────

def get_daily_report_data(category_id: Optional[int] = None,
                          date: Optional[str] = None) -> List[Dict]:
    """
    日报数据：按 url_hash GROUP BY 去重（跨引擎），
    取 full_text 最长的一条（优先 Tavily）
    """
    conditions = []
    params = []
    if category_id is not None:
        conditions.append("category_id = ?")
        params.append(category_id)
    if date:
        conditions.append("published_date LIKE ?")
        params.append(f"{date}%")

    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    if db_manager.USE_MYSQL:
        order_len = "CHAR_LENGTH(full_text)"
    else:
        order_len = "LENGTH(full_text)"

    conn = db_manager.get_conn()
    try:
        rows = db_manager.fetchall(conn, f"""
            SELECT ni.*, c.name AS category_name
            FROM news_items ni
            LEFT JOIN categories c ON ni.category_id = c.id
            INNER JOIN (
                SELECT url_hash, MAX({order_len}) AS max_len
                FROM news_items
                {where}
                GROUP BY url_hash
            ) dedup ON ni.url_hash = dedup.url_hash AND {order_len} = dedup.max_len
            {where.replace('news_items.', 'ni.').replace('WHERE', 'AND') if conditions else ''}
            ORDER BY ni.relevance_score DESC, ni.published_ts DESC
        """, tuple(params + params))
        return rows
    finally:
        conn.close()


def get_search_status() -> Dict:
    """搜索器状态信息"""
    conn = db_manager.get_conn()
    try:
        sources_count = db_manager.fetchone(conn,
            "SELECT COUNT(*) AS cnt FROM news_sources WHERE is_active = 1")
        items_today = db_manager.fetchone(conn,
            "SELECT COUNT(*) AS cnt FROM news_items WHERE fetched_at >= ?",
            (int(time.time()) - 86400,))
        engines = db_manager.fetchall(conn,
            "SELECT source_engine, COUNT(*) AS cnt FROM news_items GROUP BY source_engine")
        return {
            "active_sources": sources_count.get("cnt", 0) if sources_count else 0,
            "items_today": items_today.get("cnt", 0) if items_today else 0,
            "engine_counts": {r.get("source_engine", ""): r.get("cnt", 0) for r in engines},
        }
    finally:
        conn.close()
