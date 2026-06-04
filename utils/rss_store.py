#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
RSS 数据存储 — SQLite / MySQL
管理订阅列表和文章缓存
"""

import time
import logging
from typing import List, Dict, Optional
from utils import db_manager

logger = logging.getLogger(__name__)


def _safe_create_index(conn, index_name: str, table_name: str, columns: str):
    """安全创建索引（兼容 SQLite 和 MySQL）"""
    if db_manager.USE_MYSQL:
        # MySQL: 用 ALTER TABLE ADD INDEX IF NOT EXISTS (8.0+) 或先检查再创建
        try:
            db_manager.execute(conn,
                f"ALTER TABLE {table_name} ADD INDEX {index_name} ({columns})")
        except Exception:
            pass  # 索引已存在，忽略
    else:
        # SQLite: 支持 CREATE INDEX IF NOT EXISTS
        db_manager.execute(conn,
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name}({columns})")
    db_manager.commit(conn)


def init_db():
    """建表（幂等，兼容 SQLite 和 MySQL）"""
    conn = db_manager.get_conn()

    if db_manager.USE_MYSQL:
        # MySQL: TEXT 不能有 DEFAULT，使用 VARCHAR(500) 或去掉 DEFAULT
        db_manager.adapt_executescript(conn, """
            CREATE TABLE IF NOT EXISTS categories (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                name        VARCHAR(200) NOT NULL UNIQUE,
                description VARCHAR(500) NOT NULL DEFAULT '',
                color       VARCHAR(50) NOT NULL DEFAULT 'blue',
                sort_order  INT NOT NULL DEFAULT 0,
                created_at  INT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fakeid_blacklist (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                fakeid      VARCHAR(200) NOT NULL UNIQUE,
                nickname    VARCHAR(200) NOT NULL DEFAULT '',
                reason      VARCHAR(100) NOT NULL DEFAULT 'manual',
                verification_count INT NOT NULL DEFAULT 0,
                is_active   INT NOT NULL DEFAULT 1,
                blacklisted_at INT NOT NULL,
                unblacklisted_at INT DEFAULT NULL,
                note        VARCHAR(500) NOT NULL DEFAULT ''
            );
        """)
    else:
        db_manager.adapt_executescript(conn, """
            CREATE TABLE IF NOT EXISTS categories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                color       TEXT NOT NULL DEFAULT 'blue',
                sort_order  INTEGER NOT NULL DEFAULT 0,
                created_at  INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fakeid_blacklist (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fakeid      TEXT NOT NULL UNIQUE,
                nickname    TEXT NOT NULL DEFAULT '',
                reason      TEXT NOT NULL DEFAULT 'manual',
                verification_count INTEGER NOT NULL DEFAULT 0,
                is_active   INTEGER NOT NULL DEFAULT 1,
                blacklisted_at INTEGER NOT NULL,
                unblacklisted_at INTEGER DEFAULT NULL,
                note        TEXT NOT NULL DEFAULT ''
            );
        """)
    db_manager.commit(conn)
    _safe_create_index(conn, "idx_blacklist_active", "fakeid_blacklist", "is_active")

    # 检查 subscriptions 表是否存在
    table_exists = db_manager.check_table_exists(conn, "subscriptions")

    if table_exists:
        # 表已存在，检查是否有 category_id 列
        if not db_manager.check_column_exists(conn, "subscriptions", "category_id"):
            db_manager.add_column(conn, "subscriptions",
                                  "category_id INTEGER DEFAULT NULL")
            db_manager.commit(conn)
            logger.info("Added category_id column to subscriptions table")
    else:
        # 表不存在，创建新表
        if db_manager.USE_MYSQL:
            db_manager.adapt_executescript(conn, """
                CREATE TABLE subscriptions (
                    fakeid      VARCHAR(200) PRIMARY KEY,
                    nickname    VARCHAR(200) NOT NULL DEFAULT '',
                    alias       VARCHAR(200) NOT NULL DEFAULT '',
                    head_img    VARCHAR(500) NOT NULL DEFAULT '',
                    category_id INT DEFAULT NULL,
                    created_at  INT NOT NULL,
                    last_poll   INT NOT NULL DEFAULT 0,
                    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
                );
            """)
        else:
            db_manager.adapt_executescript(conn, """
                CREATE TABLE subscriptions (
                    fakeid      TEXT PRIMARY KEY,
                    nickname    TEXT NOT NULL DEFAULT '',
                    alias       TEXT NOT NULL DEFAULT '',
                    head_img    TEXT NOT NULL DEFAULT '',
                    category_id INTEGER DEFAULT NULL,
                    created_at  INTEGER NOT NULL,
                    last_poll   INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
                );
            """)
        db_manager.commit(conn)

    # 创建 articles 表
    if db_manager.USE_MYSQL:
        db_manager.adapt_executescript(conn, """
            CREATE TABLE IF NOT EXISTS articles (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                fakeid      VARCHAR(200) NOT NULL,
                aid         VARCHAR(200) NOT NULL DEFAULT '',
                title       VARCHAR(500) NOT NULL DEFAULT '',
                link        VARCHAR(500) NOT NULL DEFAULT '',
                digest      VARCHAR(2000) NOT NULL DEFAULT '',
                cover       VARCHAR(500) NOT NULL DEFAULT '',
                author      VARCHAR(200) NOT NULL DEFAULT '',
                content     MEDIUMTEXT NOT NULL,
                plain_content MEDIUMTEXT NOT NULL,
                publish_time INT NOT NULL DEFAULT 0,
                fetched_at  INT NOT NULL,
                source      VARCHAR(50) NOT NULL DEFAULT 'poll',
                UNIQUE(fakeid, link)
            );
        """)
    else:
        db_manager.adapt_executescript(conn, """
            CREATE TABLE IF NOT EXISTS articles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fakeid      TEXT NOT NULL,
                aid         TEXT NOT NULL DEFAULT '',
                title       TEXT NOT NULL DEFAULT '',
                link        TEXT NOT NULL DEFAULT '',
                digest      TEXT NOT NULL DEFAULT '',
                cover       TEXT NOT NULL DEFAULT '',
                author      TEXT NOT NULL DEFAULT '',
                content     TEXT NOT NULL DEFAULT '',
                plain_content TEXT NOT NULL DEFAULT '',
                publish_time INTEGER NOT NULL DEFAULT 0,
                fetched_at  INTEGER NOT NULL,
                source      TEXT NOT NULL DEFAULT 'poll',
                UNIQUE(fakeid, link),
                FOREIGN KEY (fakeid) REFERENCES subscriptions(fakeid) ON DELETE CASCADE
            );
        """)
    db_manager.commit(conn)

    _safe_create_index(conn, "idx_articles_fakeid_time",
                       "articles", "fakeid, publish_time DESC")
    _safe_create_index(conn, "idx_subscriptions_category",
                       "subscriptions", "category_id")

    # 检查并添加 source 字段（用于区分轮询器文章和历史文章）
    if db_manager.check_table_exists(conn, "articles"):
        if not db_manager.check_column_exists(conn, "articles", "source"):
            logger.info("Adding source column to articles table")
            db_manager.add_column(conn, "articles",
                                  "source VARCHAR(50) NOT NULL DEFAULT 'poll'")
            db_manager.commit(conn)
            _safe_create_index(conn, "idx_articles_source",
                               "articles", "source")
            logger.info("Added source column and index to articles table")

    db_manager.close_conn(conn)
    logger.info("RSS database initialized")


# ── 订阅管理 ─────────────────────────────────────────────

def add_subscription(fakeid: str, nickname: str = "",
                     alias: str = "", head_img: str = "") -> bool:
    conn = db_manager.get_conn()
    try:
        cursor = db_manager.execute(
            conn,
            "INSERT OR IGNORE INTO subscriptions "
            "(fakeid, nickname, alias, head_img, created_at) VALUES (?,?,?,?,?)",
            (fakeid, nickname, alias, head_img, int(time.time())),
        )
        db_manager.commit(conn)
        return cursor.rowcount > 0
    finally:
        db_manager.close_conn(conn)


def remove_subscription(fakeid: str) -> bool:
    conn = db_manager.get_conn()
    try:
        cursor = db_manager.execute(conn, "DELETE FROM subscriptions WHERE fakeid=?", (fakeid,))
        db_manager.commit(conn)
        return cursor.rowcount > 0
    finally:
        db_manager.close_conn(conn)


def list_subscriptions() -> List[Dict]:
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchall(
            conn,
            "SELECT s.*, c.name AS category_name, "
            "(SELECT COUNT(*) FROM articles a WHERE a.fakeid=s.fakeid) AS article_count "
            "FROM subscriptions s "
            "LEFT JOIN categories c ON s.category_id = c.id "
            "ORDER BY s.created_at DESC",
        )
    finally:
        db_manager.close_conn(conn)


def get_subscription(fakeid: str) -> Optional[Dict]:
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchone(
            conn,
            "SELECT * FROM subscriptions WHERE fakeid=?", (fakeid,),
        )
    finally:
        db_manager.close_conn(conn)


def update_last_poll(fakeid: str):
    conn = db_manager.get_conn()
    try:
        db_manager.execute(
            conn,
            "UPDATE subscriptions SET last_poll=? WHERE fakeid=?",
            (int(time.time()), fakeid),
        )
        db_manager.commit(conn)
    finally:
        db_manager.close_conn(conn)


# ── 文章缓存 ─────────────────────────────────────────────

def save_articles(fakeid: str, articles: List[Dict], source: str = "poll") -> int:
    """
    批量保存文章，返回新增数量。
    If an article already exists but has empty content, update it with new content.

    Args:
        fakeid: 公众号ID
        articles: 文章列表
        source: 文章来源标记，'poll'为轮询器拉取，'deep_fetch'为历史文章获取
    """
    conn = db_manager.get_conn()
    inserted = 0
    try:
        for a in articles:
            content = a.get("content", "")
            plain_content = a.get("plain_content", "")
            try:
                if db_manager.USE_MYSQL:
                    cursor = db_manager.execute(
                        conn,
                        "INSERT INTO articles "
                        "(fakeid, aid, title, link, digest, cover, author, "
                        "content, plain_content, publish_time, fetched_at, source) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE "
                        "content = CASE WHEN VALUES(content) != '' AND articles.content = '' "
                        "  THEN VALUES(content) ELSE articles.content END, "
                        "plain_content = CASE WHEN VALUES(plain_content) != '' AND articles.plain_content = '' "
                        "  THEN VALUES(plain_content) ELSE articles.plain_content END, "
                        "author = CASE WHEN VALUES(author) != '' AND articles.author = '' "
                        "  THEN VALUES(author) ELSE articles.author END",
                        (
                            fakeid,
                            a.get("aid", ""),
                            a.get("title", ""),
                            a.get("link", ""),
                            a.get("digest", ""),
                            a.get("cover", ""),
                            a.get("author", ""),
                            content,
                            plain_content,
                            a.get("publish_time", 0),
                            int(time.time()),
                            source,
                        ),
                    )
                else:
                    cursor = db_manager.execute(
                        conn,
                        "INSERT INTO articles "
                        "(fakeid, aid, title, link, digest, cover, author, "
                        "content, plain_content, publish_time, fetched_at, source) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(fakeid, link) DO UPDATE SET "
                        "content = CASE WHEN excluded.content != '' AND articles.content = '' "
                        "  THEN excluded.content ELSE articles.content END, "
                        "plain_content = CASE WHEN excluded.plain_content != '' AND articles.plain_content = '' "
                        "  THEN excluded.plain_content ELSE articles.plain_content END, "
                        "author = CASE WHEN excluded.author != '' AND articles.author = '' "
                        "  THEN excluded.author ELSE articles.author END",
                        (
                            fakeid,
                            a.get("aid", ""),
                            a.get("title", ""),
                            a.get("link", ""),
                            a.get("digest", ""),
                            a.get("cover", ""),
                            a.get("author", ""),
                            content,
                            plain_content,
                            a.get("publish_time", 0),
                            int(time.time()),
                            source,
                        ),
                    )
                if cursor.rowcount > 0:
                    inserted += 1
            except db_manager.IntegrityError:
                pass
        db_manager.commit(conn)
        return inserted
    finally:
        db_manager.close_conn(conn)


def get_articles(fakeid: str, limit: int = 20) -> List[Dict]:
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchall(
            conn,
            "SELECT * FROM articles WHERE fakeid=? "
            "ORDER BY publish_time DESC LIMIT ?",
            (fakeid, limit),
        )
    finally:
        db_manager.close_conn(conn)


def get_regular_articles(fakeid: str, limit: int = 50) -> List[Dict]:
    """
    获取常规文章（轮询器拉取的文章）
    只返回 source='poll' 的文章，不包含历史文章
    """
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchall(
            conn,
            "SELECT * FROM articles WHERE fakeid=? AND source='poll' "
            "ORDER BY publish_time DESC LIMIT ?",
            (fakeid, limit),
        )
    finally:
        db_manager.close_conn(conn)


def get_historical_articles(fakeid: str, limit: int = 500, offset: int = 0) -> List[Dict]:
    """
    获取历史文章（通过"获取历史文章"功能拉取的文章）
    返回 source='deep_fetch' 的文章，用于独立的历史 RSS，支持分页
    """
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchall(
            conn,
            "SELECT * FROM articles WHERE fakeid=? AND source='deep_fetch' "
            "ORDER BY publish_time DESC LIMIT ? OFFSET ?",
            (fakeid, limit, offset),
        )
    finally:
        db_manager.close_conn(conn)


def count_historical_articles(fakeid: str) -> int:
    """统计历史文章数量（source='deep_fetch'的文章）"""
    conn = db_manager.get_conn()
    try:
        row = db_manager.fetchone(
            conn,
            "SELECT COUNT(*) as cnt FROM articles WHERE fakeid=? AND source='deep_fetch'",
            (fakeid,),
        )
        return row["cnt"] if row else 0
    finally:
        db_manager.close_conn(conn)


def get_all_fakeids() -> List[str]:
    conn = db_manager.get_conn()
    try:
        rows = db_manager.fetchall(conn, "SELECT fakeid FROM subscriptions")
        return [r["fakeid"] for r in rows]
    finally:
        db_manager.close_conn(conn)


def get_all_articles(limit: int = 50) -> List[Dict]:
    """
    获取所有订阅的常规文章（聚合RSS）
    只返回轮询器拉取的文章（source='poll'），不包含历史文章

    [2026-05-06 优化] 使用窗口函数实现"每号限额 + 总数限制"策略：
    - 根据订阅数量动态调整每个号的文章数限制
    - 保证每个订阅号都有文章显示（避免活跃号占满）
    - 单订阅场景与单个 RSS 保持一致
    """
    conn = db_manager.get_conn()
    try:
        # 获取所有订阅的fakeid
        subs = db_manager.fetchall(conn, "SELECT fakeid FROM subscriptions")
        if not subs:
            return []

        fakeid_list = [s["fakeid"] for s in subs]
        subscription_count = len(fakeid_list)

        # 根据订阅数量计算动态限制
        per_sub_limit, total_limit = _calculate_aggregated_limits(subscription_count)

        # 使用实际的 limit 参数作为总数上限（用户可自定义）
        total_limit = min(limit, total_limit)

        placeholders = ",".join("?" * len(fakeid_list))

        # 使用窗口函数：每个订阅号最多 N 篇，总共最多 M 篇
        rows = db_manager.fetchall(
            conn,
            f"""
            WITH ranked_articles AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY fakeid
                        ORDER BY publish_time DESC
                    ) AS rn
                FROM articles
                WHERE fakeid IN ({placeholders}) AND source='poll'
            )
            SELECT * FROM ranked_articles
            WHERE rn <= ?
            ORDER BY publish_time DESC
            LIMIT ?
            """,
            (*fakeid_list, per_sub_limit, total_limit),
        )

        return rows
    finally:
        db_manager.close_conn(conn)


def _calculate_aggregated_limits(subscription_count: int) -> tuple:
    """
    根据订阅数量动态计算聚合 RSS 的限制策略

    Args:
        subscription_count: 订阅数量

    Returns:
        (per_sub_limit, total_limit): 每个订阅号的限额、总数上限

    策略设计：
    - 每个订阅号统一 30 篇
    - total_limit = subscription_count * 30（精确计算）
    - 最高支持 4500 篇（150 订阅 * 30）
    """
    if subscription_count == 0:
        return (0, 0)

    per_sub_limit = 30
    total_limit = subscription_count * 30

    # 上限：4500 篇（对应 150 个订阅）
    if total_limit > 4500:
        total_limit = 4500

    return (per_sub_limit, total_limit)


# ── 黑名单管理 ─────────────────────────────────────────────

def add_to_blacklist(fakeid: str, nickname: str = "", reason: str = "manual",
                     verification_count: int = 0, note: str = "") -> bool:
    """添加公众号到黑名单"""
    conn = db_manager.get_conn()
    try:
        db_manager.execute(
            conn,
            "INSERT OR REPLACE INTO fakeid_blacklist "
            "(fakeid, nickname, reason, verification_count, is_active, blacklisted_at, note) "
            "VALUES (?,?,?,?,1,?,?)",
            (fakeid, nickname, reason, verification_count, int(time.time()), note),
        )
        db_manager.commit(conn)
        logger.info("Added %s to blacklist: %s", fakeid[:8], reason)
        return True
    finally:
        db_manager.close_conn(conn)


def remove_from_blacklist(fakeid: str) -> bool:
    """从黑名单移除（标记为非活跃）"""
    conn = db_manager.get_conn()
    try:
        cursor = db_manager.execute(
            conn,
            "UPDATE fakeid_blacklist SET is_active=0, unblacklisted_at=? WHERE fakeid=?",
            (int(time.time()), fakeid),
        )
        db_manager.commit(conn)
        return cursor.rowcount > 0
    finally:
        db_manager.close_conn(conn)


def delete_blacklist_record(blacklist_id: int) -> bool:
    """永久删除黑名单记录"""
    conn = db_manager.get_conn()
    try:
        cursor = db_manager.execute(conn, "DELETE FROM fakeid_blacklist WHERE id=? AND is_active=0", (blacklist_id,))
        db_manager.commit(conn)
        return cursor.rowcount > 0
    finally:
        db_manager.close_conn(conn)


def is_blacklisted(fakeid: str) -> bool:
    """检查公众号是否在黑名单中"""
    conn = db_manager.get_conn()
    try:
        row = db_manager.fetchone(
            conn,
            "SELECT 1 FROM fakeid_blacklist WHERE fakeid=? AND is_active=1",
            (fakeid,),
        )
        return row is not None
    finally:
        db_manager.close_conn(conn)


def get_blacklist() -> List[Dict]:
    """获取黑名单列表"""
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchall(
            conn,
            "SELECT * FROM fakeid_blacklist ORDER BY blacklisted_at DESC",
        )
    finally:
        db_manager.close_conn(conn)


def get_active_blacklist_fakeids() -> List[str]:
    """获取活跃黑名单的 fakeid 列表"""
    conn = db_manager.get_conn()
    try:
        rows = db_manager.fetchall(
            conn,
            "SELECT fakeid FROM fakeid_blacklist WHERE is_active=1",
        )
        return [r["fakeid"] for r in rows]
    finally:
        db_manager.close_conn(conn)


def increment_verification_count(fakeid: str, nickname: str = "") -> int:
    """
    增加验证码触发次数，达到阈值时自动加入黑名单

    [2026-05-18 优化]
    1. 阈值 5 → 8（避免误判，配合精确化 verification 检测后误报率本就低）
    2. 修复隐藏 bug：之前 UPDATE 强制 is_active=1 → admin 手动取消后，下次触发又被自动激活
       现在：仅在「跨阈值的瞬间」激活；已激活/已被 admin 取消的状态保持不变

    注意：本计数为永久累计（无 24h 窗口）。误判的 fakeid 可通过 remove_from_blacklist
    或 delete_blacklist_record 手动清理（开源版简化设计，不引入 PG/Redis）

    返回：当前触发次数
    """
    threshold = 8
    conn = db_manager.get_conn()
    try:
        row = db_manager.fetchone(
            conn,
            "SELECT * FROM fakeid_blacklist WHERE fakeid=?", (fakeid,),
        )

        if row:
            new_count = row["verification_count"] + 1
            crossing_threshold = (
                row["verification_count"] < threshold <= new_count
                and not row["is_active"]
            )
            if crossing_threshold:
                # 首次跨过阈值：激活拉黑
                db_manager.execute(
                    conn,
                    "UPDATE fakeid_blacklist SET verification_count=?, is_active=1, "
                    "blacklisted_at=?, note=? WHERE fakeid=?",
                    (new_count, int(time.time()),
                     f"自动记录: 触发验证码 {new_count} 次（达到阈值 {threshold}）",
                     fakeid),
                )
            else:
                # 仅累计计数，不动 is_active（保留 admin 手动取消的状态）
                db_manager.execute(
                    conn,
                    "UPDATE fakeid_blacklist SET verification_count=? WHERE fakeid=?",
                    (new_count, fakeid),
                )
        else:
            new_count = 1
            db_manager.execute(
                conn,
                "INSERT INTO fakeid_blacklist "
                "(fakeid, nickname, reason, verification_count, is_active, blacklisted_at, note) "
                "VALUES (?,?,?,?,?,?,?)",
                (fakeid, nickname, "high_verification", new_count,
                 1 if new_count >= threshold else 0,
                 int(time.time()),
                 f"自动记录: 触发验证码 {new_count} 次"),
            )

        db_manager.commit(conn)

        if new_count >= threshold:
            logger.warning("Fakeid %s reached %d verification triggers (threshold=%d)",
                          fakeid[:8], new_count, threshold)

        return new_count
    finally:
        db_manager.close_conn(conn)


# ── 分类管理 ─────────────────────────────────────────────

def create_category(name: str, description: str = "", color: str = "blue") -> Optional[int]:
    """创建分类，返回新分类 ID"""
    conn = db_manager.get_conn()
    try:
        # 获取最大 sort_order
        row = db_manager.fetchone(conn, "SELECT MAX(sort_order) as max_order FROM categories")
        max_order = row["max_order"] or 0

        cursor = db_manager.execute(
            conn,
            "INSERT INTO categories (name, description, color, sort_order, created_at) "
            "VALUES (?,?,?,?,?)",
            (name, description, color, max_order + 1, int(time.time())),
        )
        db_manager.commit(conn)
        return db_manager.lastrowid(cursor)
    except db_manager.IntegrityError:
        return None
    finally:
        db_manager.close_conn(conn)


def update_category(category_id: int, name: str = None,
                    description: str = None, color: str = None) -> bool:
    """更新分类"""
    conn = db_manager.get_conn()
    try:
        updates = []
        params = []
        if name is not None:
            updates.append("name=?")
            params.append(name)
        if description is not None:
            updates.append("description=?")
            params.append(description)
        if color is not None:
            updates.append("color=?")
            params.append(color)

        if not updates:
            return False

        params.append(category_id)
        cursor = db_manager.execute(
            conn,
            f"UPDATE categories SET {', '.join(updates)} WHERE id=?",
            params,
        )
        db_manager.commit(conn)
        return cursor.rowcount > 0
    finally:
        db_manager.close_conn(conn)


def delete_category(category_id: int) -> bool:
    """删除分类（订阅会自动解除关联）"""
    conn = db_manager.get_conn()
    try:
        cursor = db_manager.execute(conn, "DELETE FROM categories WHERE id=?", (category_id,))
        db_manager.commit(conn)
        return cursor.rowcount > 0
    finally:
        db_manager.close_conn(conn)


def list_categories() -> List[Dict]:
    """获取所有分类及其订阅数"""
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchall(conn, """
            SELECT c.*,
                   (SELECT COUNT(*) FROM subscriptions s WHERE s.category_id=c.id) AS subscription_count
            FROM categories c
            ORDER BY c.sort_order, c.created_at
        """)
    finally:
        db_manager.close_conn(conn)


def get_category(category_id: int) -> Optional[Dict]:
    """获取单个分类"""
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchone(
            conn,
            "SELECT * FROM categories WHERE id=?", (category_id,),
        )
    finally:
        db_manager.close_conn(conn)


def set_subscription_category(fakeid: str, category_id: Optional[int]) -> bool:
    """设置订阅的分类"""
    conn = db_manager.get_conn()
    try:
        cursor = db_manager.execute(
            conn,
            "UPDATE subscriptions SET category_id=? WHERE fakeid=?",
            (category_id, fakeid),
        )
        db_manager.commit(conn)
        return cursor.rowcount > 0
    finally:
        db_manager.close_conn(conn)


def get_subscriptions_by_category(category_id: int) -> List[Dict]:
    """获取分类下的所有订阅"""
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchall(
            conn,
            "SELECT s.*, "
            "(SELECT COUNT(*) FROM articles a WHERE a.fakeid=s.fakeid) AS article_count "
            "FROM subscriptions s WHERE s.category_id=? ORDER BY s.created_at DESC",
            (category_id,),
        )
    finally:
        db_manager.close_conn(conn)


def get_articles_by_category(category_id: int, limit: int = 50) -> List[Dict]:
    """
    获取分类下所有订阅的常规文章
    只返回轮询器拉取的文章（source='poll'），不包含历史文章

    [2026-05-06 优化] 使用窗口函数实现"每号限额 + 总数限制"策略
    """
    conn = db_manager.get_conn()
    try:
        # 获取该分类下的所有fakeid
        subs = db_manager.fetchall(
            conn,
            "SELECT fakeid FROM subscriptions WHERE category_id=?",
            (category_id,),
        )
        if not subs:
            return []

        fakeid_list = [s["fakeid"] for s in subs]
        subscription_count = len(fakeid_list)

        # [2026-05-06 优化] 使用窗口函数实现"每号限额 + 总数限制"策略
        # 根据订阅数量计算动态限制
        per_sub_limit, total_limit = _calculate_aggregated_limits(subscription_count)
        # 使用实际的 limit 参数作为总数上限（用户可自定义）
        total_limit = min(limit, total_limit)

        placeholders = ",".join("?" * len(fakeid_list))

        # 使用窗口函数：每个订阅号最多 N 篇，总共最多 M 篇
        return db_manager.fetchall(
            conn,
            f"""
            WITH ranked_articles AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY fakeid
                        ORDER BY publish_time DESC
                    ) AS rn
                FROM articles
                WHERE fakeid IN ({placeholders}) AND source='poll'
            )
            SELECT * FROM ranked_articles
            WHERE rn <= ?
            ORDER BY publish_time DESC
            LIMIT ?
            """,
            (*fakeid_list, per_sub_limit, total_limit),
        )
    finally:
        db_manager.close_conn(conn)


# ── 文章库查询 ─────────────────────────────────────────────

def list_cached_articles(limit: int = 20, offset: int = 0,
                         fakeid: str = None, source: str = None,
                         keyword: str = None) -> list:
    conn = db_manager.get_conn()
    try:
        conditions = []
        params = []
        if fakeid:
            conditions.append("a.fakeid = ?")
            params.append(fakeid)
        if source:
            conditions.append("a.source = ?")
            params.append(source)
        if keyword:
            kw = f"%{keyword}%"
            conditions.append("(a.title LIKE ? OR a.digest LIKE ? OR a.author LIKE ? OR a.plain_content LIKE ?)")
            params.extend([kw, kw, kw, kw])
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        return db_manager.fetchall(
            conn,
            f"SELECT a.*, s.nickname FROM articles a LEFT JOIN subscriptions s ON a.fakeid = s.fakeid{where} ORDER BY a.publish_time DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
    finally:
        db_manager.close_conn(conn)


def count_cached_articles(fakeid: str = None, source: str = None, keyword: str = None) -> int:
    conn = db_manager.get_conn()
    try:
        conditions = []
        params = []
        if fakeid:
            conditions.append("fakeid = ?")
            params.append(fakeid)
        if source:
            conditions.append("source = ?")
            params.append(source)
        if keyword:
            kw = f"%{keyword}%"
            conditions.append("(title LIKE ? OR digest LIKE ? OR author LIKE ? OR plain_content LIKE ?)")
            params.extend([kw, kw, kw, kw])
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        row = db_manager.fetchone(conn, f"SELECT COUNT(*) as cnt FROM articles{where}", tuple(params))
        return row["cnt"] if row else 0
    finally:
        db_manager.close_conn(conn)


def get_cached_article(article_id: int) -> Optional[Dict]:
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchone(conn, "SELECT * FROM articles WHERE id = ?", (article_id,))
    finally:
        db_manager.close_conn(conn)
