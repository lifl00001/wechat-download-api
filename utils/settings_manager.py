#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
统一配置管理器 — 支持网页端配置，持久化到数据库
优先读数据库，fallback 到环境变量，确保向后兼容
"""

import os
import time
import logging
from typing import Dict, List, Optional, Callable

from utils import db_manager

logger = logging.getLogger(__name__)

# 默认配置项定义: (key, env_var, default_value, description)
DEFAULT_SETTINGS = [
    ("site_url", "SITE_URL", "http://localhost:5000", "网站URL（用于RSS图片代理，必须配置为实际访问地址）"),
    ("rate_limit_global", "RATE_LIMIT_GLOBAL", "10", "全局限频（次/分钟）"),
    ("rate_limit_per_ip", "RATE_LIMIT_PER_IP", "5", "单IP限频（次/分钟）"),
    ("rate_limit_article_interval", "RATE_LIMIT_ARTICLE_INTERVAL", "3", "文章获取最小间隔（秒）"),
    ("rss_poll_interval", "RSS_POLL_INTERVAL", "3600", "RSS轮询间隔（秒）"),
    ("rss_scheduled_time", "RSS_SCHEDULED_TIME", "", "RSS定时执行时间（HH:MM格式，如08:00），留空则按间隔轮询"),
    ("articles_per_poll", "ARTICLES_PER_POLL", "10", "每次轮询拉取的文章批次数"),
    ("rss_fetch_full_content", "RSS_FETCH_FULL_CONTENT", "true", "RSS轮询时是否获取完整文章内容"),
    ("proxy_mode", "PROXY_MODE", "static", "代理模式：static(静态代理) 或 dynamic(动态API代理)"),
    ("proxy_urls", "PROXY_URLS", "", "SOCKS5代理池（多个用逗号分隔），静态模式时使用"),
    ("dynamic_proxy_api_url", "DYNAMIC_PROXY_API_URL", "", "动态代理提取API地址"),
    ("dynamic_proxy_refresh_interval", "DYNAMIC_PROXY_REFRESH_INTERVAL", "300", "动态代理刷新间隔（秒）"),
    ("dynamic_proxy_jq_path", "DYNAMIC_PROXY_JQ_PATH", ".data.proxy_list", "从JSON响应提取代理列表的jq路径"),
    ("dynamic_proxy_protocol", "DYNAMIC_PROXY_PROTOCOL", "socks5", "动态代理协议类型: socks5 / http / https"),
    ("dynamic_proxy_batch_mode", "DYNAMIC_PROXY_BATCH_MODE", "batch", "动态代理提取模式: batch(批量缓存) / single(每次实时提取1条)"),
    ("dynamic_proxy_format", "DYNAMIC_PROXY_FORMAT", "json", "动态代理响应格式: json(默认) / plain_text"),
    ("dynamic_proxy_separator", "DYNAMIC_PROXY_SEPARATOR", "newline", "纯文本分隔符: newline / comma / space / semicolon"),
    ("dynamic_proxy_username", "DYNAMIC_PROXY_USERNAME", "", "动态代理认证用户名（如快代理后台看到的用户名）"),
    ("dynamic_proxy_password", "DYNAMIC_PROXY_PASSWORD", "", "动态代理认证密码"),
    ("webhook_url", "WEBHOOK_URL", "", "Webhook通知地址（企业微信群机器人等）"),
    ("webhook_notification_interval", "WEBHOOK_NOTIFICATION_INTERVAL", "300", "同一事件通知最小间隔（秒）"),
    ("baidu_api_key", "BAIDU_API_KEY", "", "百度千帆 API Key"),
    ("doubao_api_key", "DOUBAO_API_KEY", "", "豆包/火山引擎联网搜索 API Key"),
    ("tavily_api_key", "TAVILY_API_KEY", "", "Tavily API Key"),
    ("news_search_interval", "NEWS_SEARCH_INTERVAL", "21600", "新闻搜索间隔（秒），默认6小时"),
    ("news_scheduled_time", "NEWS_SCHEDULED_TIME", "", "新闻定时执行时间（HH:MM格式，如07:00），留空则按间隔执行"),
]


class SettingsManager:
    """配置管理单例"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._watchers: Dict[str, List[Callable]] = {}
        self._init_table()
        self._seed_defaults()
        self._initialized = True

    def _init_table(self):
        """初始化 settings 表"""
        conn = db_manager.get_conn()
        try:
            if db_manager.USE_MYSQL:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS wechat_api_settings (
                        key_name    VARCHAR(100) PRIMARY KEY,
                        value       TEXT NOT NULL,
                        description VARCHAR(500) NOT NULL DEFAULT '',
                        updated_at  INT NOT NULL
                    )
                """)
            else:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS wechat_api_settings (
                        key_name    TEXT PRIMARY KEY,
                        value       TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '',
                        updated_at  INTEGER NOT NULL
                    )
                """)
            conn.commit()
        finally:
            conn.close()

    def _seed_defaults(self):
        """将环境变量中的默认值写入数据库（仅当数据库中不存在时）"""
        conn = db_manager.get_conn()
        try:
            for key, env_var, default, desc in DEFAULT_SETTINGS:
                row = db_manager.fetchone(
                    conn,
                    "SELECT value FROM wechat_api_settings WHERE key_name = ?",
                    (key,),
                )
                if row is None:
                    # 优先从环境变量读取，否则用内置默认值
                    env_val = os.getenv(env_var, default)
                    conn.execute(
                        "INSERT INTO wechat_api_settings (key_name, value, description, updated_at) VALUES (?, ?, ?, ?)",
                        (key, env_val, desc, int(time.time())),
                    )
                    logger.info("Settings: seeded default %s = %s", key, env_val)
            conn.commit()
        finally:
            conn.close()

    def get(self, key: str, default: str = "") -> str:
        """
        获取配置值。
        优先从数据库读取，数据库无值时 fallback 到环境变量。
        """
        conn = db_manager.get_conn()
        try:
            row = db_manager.fetchone(
                conn,
                "SELECT value FROM wechat_api_settings WHERE key_name = ?",
                (key,),
            )
            if row and row.get("value") is not None:
                return row["value"]
        finally:
            conn.close()

        # fallback: 查找对应的环境变量
        for k, env_var, d, _ in DEFAULT_SETTINGS:
            if k == key:
                return os.getenv(env_var, default)
        return default

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = self.get(key, str(default).lower()).lower()
        return val in ("true", "1", "yes", "on")

    def set(self, key: str, value: str):
        """更新配置值并触发 watcher"""
        conn = db_manager.get_conn()
        try:
            if db_manager.USE_MYSQL:
                conn.execute(
                    "INSERT INTO wechat_api_settings (key_name, value, description, updated_at) VALUES (%s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = VALUES(updated_at)",
                    (key, value, "", int(time.time())),
                )
            else:
                conn.execute(
                    "INSERT INTO wechat_api_settings (key_name, value, description, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(key_name) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (key, value, "", int(time.time())),
                )
            conn.commit()
        finally:
            conn.close()

        logger.info("Settings updated: %s = %s", key, value)
        self._notify(key, value)

    def set_many(self, items: Dict[str, str]):
        """批量更新配置"""
        conn = db_manager.get_conn()
        try:
            for key, value in items.items():
                if db_manager.USE_MYSQL:
                    conn.execute(
                        "INSERT INTO wechat_api_settings (key_name, value, description, updated_at) VALUES (%s, %s, %s, %s) "
                        "ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = VALUES(updated_at)",
                        (key, value, "", int(time.time())),
                    )
                else:
                    conn.execute(
                        "INSERT INTO wechat_api_settings (key_name, value, description, updated_at) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(key_name) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                        (key, value, "", int(time.time())),
                    )
            conn.commit()
        finally:
            conn.close()

        for key, value in items.items():
            logger.info("Settings updated: %s = %s", key, value)
            self._notify(key, value)

    def list_all(self) -> List[Dict]:
        """获取所有配置项"""
        conn = db_manager.get_conn()
        try:
            rows = db_manager.fetchall(conn, "SELECT * FROM wechat_api_settings ORDER BY key_name")
            return rows
        finally:
            conn.close()

    def register_watcher(self, key: str, callback: Callable):
        """注册配置变更回调 key 支持 '*' 表示监听所有变更"""
        if key not in self._watchers:
            self._watchers[key] = []
        self._watchers[key].append(callback)

    def _notify(self, key: str, value: str):
        """触发 watcher"""
        # 精确匹配
        for cb in self._watchers.get(key, []):
            try:
                cb(key, value)
            except Exception as e:
                logger.error("Settings watcher error for %s: %s", key, e)
        # 通配符监听
        for cb in self._watchers.get("*", []):
            try:
                cb(key, value)
            except Exception as e:
                logger.error("Settings wildcard watcher error: %s", e)


# 全局单例
settings_manager = SettingsManager()
