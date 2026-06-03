#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
数据库连接管理 — 支持 SQLite 和 MySQL 双后端
根据环境变量自动切换，保持向后兼容
"""

import os
import sqlite3
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# ── 配置读取 ─────────────────────────────────────────────

DB_HOST = os.getenv("DB_HOST", "").strip()
DB_PORT = int(os.getenv("DB_PORT", "3306") or "3306")
DB_DATABASE = os.getenv("DB_DATABASE", "").strip()
DB_USERNAME = os.getenv("DB_USERNAME", "").strip()
DB_PASSWORD = os.getenv("DB_PASSWORD", "").strip()

USE_MYSQL = bool(DB_HOST and DB_DATABASE and DB_USERNAME)

# SQLite 默认路径
_default_db = Path(__file__).parent.parent / "data" / "rss.db"
SQLITE_DB_PATH = Path(os.getenv("RSS_DB_PATH", str(_default_db)))

# 懒加载 MySQL 模块
_pymysql = None
_DictCursor = None

# 统一 IntegrityError
IntegrityError = (sqlite3.IntegrityError,)
try:
    import pymysql.err
    IntegrityError = (sqlite3.IntegrityError, pymysql.err.IntegrityError)
except ImportError:
    pass


def _load_mysql():
    """懒加载 pymysql"""
    global _pymysql, _DictCursor
    if _pymysql is None:
        try:
            import pymysql as pm
            from pymysql.cursors import DictCursor as DC
            _pymysql = pm
            _DictCursor = DC
        except ImportError as e:
            logger.error("pymysql not installed: %s", e)
            raise


def get_conn():
    """
    获取数据库连接。
    根据环境变量自动选择 SQLite 或 MySQL。
    """
    if USE_MYSQL:
        return _get_mysql_conn()
    return _get_sqlite_conn()


def _get_sqlite_conn():
    """获取 SQLite 连接"""
    SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SQLITE_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class _MySQLConnectionWrapper:
    """
    给 pymysql 连接添加 sqlite3 风格的接口，使 rss_store.py 改动最小化。
    """
    def __init__(self, conn):
        self._conn = conn
    
    def execute(self, sql, params=()):
        adapted = adapt_sql(sql)
        cursor = self._conn.cursor()
        cursor.execute(adapted, params)
        return cursor
    
    def executescript(self, sql):
        adapt_executescript(self._conn, sql)
    
    def commit(self):
        self._conn.commit()
    
    def close(self):
        self._conn.close()
    
    @property
    def total_changes(self):
        # MySQL 没有 total_changes，调用方应使用 cursor.rowcount
        return 0
    
    def cursor(self):
        return self._conn.cursor()


def _get_mysql_conn():
    """获取 MySQL 连接"""
    _load_mysql()
    raw_conn = _pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_DATABASE,
        user=DB_USERNAME,
        password=DB_PASSWORD,
        charset="utf8mb4",
        cursorclass=_DictCursor,
        autocommit=False,
    )
    return _MySQLConnectionWrapper(raw_conn)


def close_conn(conn):
    """安全关闭连接"""
    try:
        conn.close()
    except Exception:
        pass


def row_to_dict(row: Any) -> Dict:
    """统一将查询结果行转为 dict"""
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if isinstance(row, sqlite3.Row):
        return dict(row)
    # pymysql DictCursor 返回的是 dict
    return dict(row)


def rows_to_dicts(rows: List[Any]) -> List[Dict]:
    """批量转换"""
    return [row_to_dict(r) for r in rows]


# ── SQL 方言适配 ─────────────────────────────────────────

def adapt_sql(sql: str) -> str:
    """
    将 SQLite SQL 适配为 MySQL SQL。
    注意：仅处理简单的语法差异，复杂的 ON CONFLICT 需要在调用处做条件判断。
    """
    if not USE_MYSQL:
        return sql

    # 1. AUTOINCREMENT -> AUTO_INCREMENT
    sql = sql.replace("AUTOINCREMENT", "AUTO_INCREMENT")

    # 2. INTEGER PRIMARY KEY AUTO_INCREMENT -> INT AUTO_INCREMENT PRIMARY KEY
    # 需要先处理这种特殊顺序
    import re
    sql = re.sub(
        r"INTEGER\s+PRIMARY\s+KEY\s+AUTO_INCREMENT",
        "INT AUTO_INCREMENT PRIMARY KEY",
        sql,
        flags=re.IGNORECASE,
    )

    # 3. INSERT OR IGNORE -> INSERT IGNORE
    sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT IGNORE INTO", sql, flags=re.IGNORECASE)

    # 4. INSERT OR REPLACE -> REPLACE INTO
    sql = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "REPLACE INTO", sql, flags=re.IGNORECASE)

    # 5. ? -> %s (参数占位符)
    # 注意：只替换不在字符串字面量中的 ?
    # 简单处理：逐个替换，假设 SQL 中没有字符串字面量包含 ?
    # 更安全的做法：由于我们的 SQL 都是代码里硬编码的，没有用户输入的 ?，所以可以直接替换
    sql = sql.replace("?", "%s")

    return sql


def adapt_executescript(conn, sql_script: str):
    """
    执行多段 SQL 脚本。
    SQLite 用 executescript，MySQL 需要逐条执行。
    """
    if USE_MYSQL:
        # MySQL 不支持 executescript，需要拆分执行
        # 按分号分割，过滤空语句和注释
        statements = []
        current = []
        for line in sql_script.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            current.append(line)
            if stripped.endswith(";"):
                stmt = "\n".join(current).strip()
                if stmt:
                    statements.append(stmt)
                current = []
        if current:
            stmt = "\n".join(current).strip()
            if stmt:
                statements.append(stmt)

        cursor = conn.cursor()
        for stmt in statements:
            adapted = adapt_sql(stmt)
            if adapted.strip():
                try:
                    cursor.execute(adapted)
                except Exception as e:
                    logger.warning("MySQL execute warning: %s | SQL: %s", e, adapted[:200])
        conn.commit()
        cursor.close()
    else:
        conn.executescript(sql_script)


def execute(conn, sql: str, params: tuple = ()):
    """执行 SQL，返回 cursor"""
    adapted_sql = adapt_sql(sql)
    cursor = conn.cursor()
    cursor.execute(adapted_sql, params)
    return cursor


def fetchall(conn, sql: str, params: tuple = ()) -> List[Dict]:
    """执行查询，返回 dict 列表"""
    cursor = execute(conn, sql, params)
    rows = cursor.fetchall()
    cursor.close()
    return rows_to_dicts(rows)


def fetchone(conn, sql: str, params: tuple = ()) -> Optional[Dict]:
    """执行查询，返回单个 dict 或 None"""
    cursor = execute(conn, sql, params)
    row = cursor.fetchone()
    cursor.close()
    return row_to_dict(row) if row else None


def commit(conn):
    """提交事务"""
    conn.commit()


def total_changes(conn) -> int:
    """获取最近一次操作影响的行数"""
    if USE_MYSQL:
        # MySQL 通过 cursor.rowcount 获取，但连接对象没有 total_changes
        # 这里返回 0，调用方应使用 cursor.rowcount
        return 0
    return conn.total_changes


def lastrowid(cursor) -> int:
    """获取最后插入的行 ID"""
    if USE_MYSQL:
        return cursor.lastrowid
    return cursor.lastrowid


# ── MySQL 特有的表结构检查 ───────────────────────────────

def check_table_exists(conn, table_name: str) -> bool:
    """检查表是否存在"""
    if USE_MYSQL:
        row = fetchone(conn, "SHOW TABLES LIKE %s", (table_name,))
        return row is not None and len(row) > 0
    else:
        row = fetchone(
            conn,
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return row is not None


def check_column_exists(conn, table_name: str, column_name: str) -> bool:
    """检查列是否存在"""
    if USE_MYSQL:
        rows = fetchall(conn, "SHOW COLUMNS FROM {}".format(table_name))
        return any(r.get("Field") == column_name for r in rows)
    else:
        rows = fetchall(conn, "PRAGMA table_info({})".format(table_name))
        return any(r.get("name") == column_name for r in rows)


def add_column(conn, table_name: str, column_def: str):
    """添加列"""
    sql = f"ALTER TABLE {table_name} ADD COLUMN {column_def}"
    cursor = execute(conn, sql)
    cursor.close()
