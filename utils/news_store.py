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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional
from utils import db_manager

logger = logging.getLogger(__name__)


def parse_date_to_ts(published_date: str) -> int:
    """
    把 published_date 字符串解析成 Unix 时间戳（秒）。

    支持的格式（按匹配顺序）：
      1. RFC822 / RFC1123：'Mon, 15 Jun 2026 00:02:43 GMT'（tavily 返回这种）
         —— 用 email.utils.parsedate_to_datetime，正确处理 GMT/时区偏移，
            解析结果按 UTC 解释，避免本地时区带来的 8 小时偏差。
      2. ISO8601：'2026-06-15T00:02:43' / '2026-06-15T00:02:43Z'
      3. 标准：'2026-06-15 00:02:43' / '2026-06-15'

    解析失败返回 0（调用方据此决定是否保留为空）。
    """
    if not published_date or not isinstance(published_date, str):
        return 0
    s = published_date.strip()
    if not s:
        return 0

    # 1) RFC822 / RFC1123（带星期/月份缩写，常见于 HTTP/RSS/tavily）
    #    特征：含逗号或含 GMT/Z 时区标记
    if "," in s or "GMT" in s or s.endswith("Z") and "T" not in s:
        try:
            dt = parsedate_to_datetime(s)
            if dt is not None:
                # parsedate_to_datetime 对 naive 结果按本地时区解释，需统一为 UTC 时间戳
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
        except (TypeError, ValueError):
            pass

    # 2) ISO8601 / 标准日期：截取前19位逐格式尝试
    head = s[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(head, fmt)
            # 无时区信息时按本地时区解释（与历史数据行为一致）
            return int(dt.timestamp())
        except ValueError:
            continue

    return 0


def normalize_published_date(published_date: str, published_ts: int = 0) -> str:
    """
    把 published_date 归一化成统一的本地时间字符串：'YYYY-MM-DD HH:MM:SS'。

    规则（保证 published_date 与 published_ts 始终一致）：
      - 若已有 published_ts（>0），直接用它格式化 → 最可靠
      - 否则用 parse_date_to_ts() 从字符串解析出 ts 再格式化
      - 解析失败（ts=0）则原样返回（保留原始信息，不丢失）

    处理的输入格式：
      - RFC822：'Mon, 15 Jun 2026 00:02:43 GMT'  → '2026-06-15 08:02:43'
      - ISO8601：'2026-06-15T00:02:43'           → '2026-06-15 00:02:43'
      - 仅日期：'2026-06-15'                      → '2026-06-15 00:00:00'
      - 已标准：'2026-06-15 00:02:43'             → 原样返回
    """
    if not published_date or not isinstance(published_date, str):
        return ""

    s = published_date.strip()
    if not s:
        return ""

    # 已经是目标格式（标准 YYYY-MM-DD HH:MM:SS），直接返回
    if len(s) == 19 and s[4] == "-" and s[7] == "-" and s[10] == " " and s[13] == ":" and s[16] == ":":
        return s

    # 优先用现成的 ts（更可靠），否则从字符串解析
    ts = published_ts if published_ts and published_ts > 0 else parse_date_to_ts(s)
    if ts > 0:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    # 都失败：原样返回（保留原始信息便于排查）
    return s


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
                    title           TEXT NOT NULL,
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
        _safe_create_index(conn, "idx_news_items_engine", "news_items", "source_engine")
        _safe_create_index(conn, "idx_news_items_published_date", "news_items", "published_date")
        _safe_create_index(conn, "idx_news_items_fetched", "news_items", "fetched_at DESC")
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

            # 解析 published_ts（支持 RFC822/ISO8601/标准日期，解析失败为 0）
            published_ts = item.get("published_ts", 0)
            if not published_ts and published_date:
                published_ts = parse_date_to_ts(published_date)

            # 归一化 published_date 为统一的 'YYYY-MM-DD HH:MM:SS' 本地时间格式
            # （基于已解析的 ts，保证 date 与 ts 始终一致）
            if published_date:
                published_date = normalize_published_date(published_date, published_ts)

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
                    # MySQL strict 模式下，单条失败会让连接进入 rollback-only 状态，
                    # 若不立即 rollback 清除污染，最终整批 commit 实际是回滚，
                    # 导致"前台显示搜索成功，实际一条都没入库"。
                    db_manager.commit(conn)
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
                    db_manager.commit(conn)
                saved += 1
            except db_manager.IntegrityError:
                db_manager.rollback(conn)
            except Exception as e:
                # 单条失败立即回滚，清除 rollback-only 污染状态，
                # 让后续条目可继续写入（关键修复点）
                db_manager.rollback(conn)
                logger.warning("Failed to save news item %s: %s", url[:80], e)

        # 更新 source 的 last_search_at（独立于条目保存，单独提交）
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
        # 默认不返回 full_text 以提升性能，需要时单独查询
        rows = db_manager.fetchall(conn, f"""
            SELECT ni.id, ni.source_id, ni.title, ni.url, ni.snippet,
                   ni.source_engine, ni.source_name, ni.author,
                   ni.published_date, ni.published_ts, ni.relevance_score,
                   ni.category_id, ni.url_hash, ni.fetched_at,
                   ns.name AS source_name, c.name AS category_name
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


def get_news_item(item_id: int, include_full_text: bool = False) -> Optional[Dict]:
    """获取单条新闻详情"""
    conn = db_manager.get_conn()
    try:
        if include_full_text:
            # 需要全文时才查询 full_text
            return db_manager.fetchone(conn, """
                SELECT ni.*, ns.name AS source_name, c.name AS category_name
                FROM news_items ni
                LEFT JOIN news_sources ns ON ni.source_id = ns.id
                LEFT JOIN categories c ON ni.category_id = c.id
                WHERE ni.id = ?
            """, (item_id,))
        else:
            # 默认不返回 full_text
            return db_manager.fetchone(conn, """
                SELECT ni.id, ni.source_id, ni.title, ni.url, ni.snippet,
                       ni.source_engine, ni.source_name, ni.author,
                       ni.published_date, ni.published_ts, ni.relevance_score,
                       ni.category_id, ni.url_hash, ni.fetched_at,
                       ns.name AS source_name, c.name AS category_name
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
    日报数据：按 url_hash 去重（跨引擎），
    优先取 Tavily 引擎数据（通常有更完整的全文）
    """
    conditions = []
    params = []
    if category_id is not None:
        conditions.append("ni.category_id = ?")
        params.append(category_id)
    if date:
        conditions.append("ni.published_date LIKE ?")
        params.append(f"{date}%")

    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    conn = db_manager.get_conn()
    try:
        # 使用 ROW_NUMBER() 去重，优先 Tavily 引擎，然后按相关度和时间排序
        if db_manager.USE_MYSQL:
            # MySQL 8.0+ 支持窗口函数
            rows = db_manager.fetchall(conn, f"""
                SELECT * FROM (
                    SELECT
                        ni.id, ni.source_id, ni.title, ni.url, ni.snippet,
                        ni.source_engine, ni.source_name, ni.author,
                        ni.published_date, ni.published_ts, ni.relevance_score,
                        ni.category_id, ni.url_hash, ni.fetched_at,
                        c.name AS category_name,
                        ROW_NUMBER() OVER (
                            PARTITION BY ni.url_hash
                            ORDER BY
                                CASE WHEN ni.source_engine = 'tavily' THEN 0 ELSE 1 END,
                                ni.relevance_score DESC,
                                ni.published_ts DESC
                        ) AS rn
                    FROM news_items ni
                    LEFT JOIN categories c ON ni.category_id = c.id
                    {where}
                ) ranked
                WHERE rn = 1
                ORDER BY relevance_score DESC, published_ts DESC
            """, tuple(params))
        else:
            # SQLite 不支持窗口函数，使用子查询去重
            rows = db_manager.fetchall(conn, f"""
                SELECT ni.id, ni.source_id, ni.title, ni.url, ni.snippet,
                       ni.source_engine, ni.source_name, ni.author,
                       ni.published_date, ni.published_ts, ni.relevance_score,
                       ni.category_id, ni.url_hash, ni.fetched_at,
                       c.name AS category_name
                FROM news_items ni
                LEFT JOIN categories c ON ni.category_id = c.id
                {where}
                AND ni.id = (
                    SELECT id FROM news_items ni2
                    WHERE ni2.url_hash = ni.url_hash
                    ORDER BY
                        CASE WHEN ni2.source_engine = 'tavily' THEN 0 ELSE 1 END,
                        ni2.relevance_score DESC,
                        ni2.published_ts DESC
                    LIMIT 1
                )
                ORDER BY ni.relevance_score DESC, ni.published_ts DESC
            """, tuple(params))
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
