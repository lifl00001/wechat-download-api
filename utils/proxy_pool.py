#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
代理池管理
支持两种模式：
1. 静态代理模式：用户配置固定代理列表，轮转使用
2. 动态API代理模式：定时调用供应商API获取代理列表，代理自动刷新

配置方式（.env）：
    PROXY_MODE=static
    PROXY_URLS=socks5://ip1:port,http://ip2:port

    # 或动态模式
    PROXY_MODE=dynamic
    DYNAMIC_PROXY_API_URL=http://api.provider.com/get?num=10
    DYNAMIC_PROXY_REFRESH_INTERVAL=300
    DYNAMIC_PROXY_JQ_PATH=.data.proxy_list

留空则不使用代理。
"""

import logging
import time
import threading
from typing import Optional, List, Dict, Any

from utils.settings_manager import settings_manager

logger = logging.getLogger(__name__)

FAIL_COOLDOWN = 120  # fixed cooldown, not from settings


class ProxyPool:
    """带健康检测的代理池，支持静态和动态两种模式"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._proxies: List[str] = []
        self._index = 0
        self._fail_until: dict[str, float] = {}
        self._lock = threading.Lock()

        # 动态代理相关
        self._dynamic_proxies: List[Dict[str, Any]] = []  # {"url": str, "expire_at": float}
        self._dynamic_index = 0
        self._dynamic_refresh_task: Optional[threading.Timer] = None
        self._dynamic_last_refresh = 0
        self._dynamic_last_error = ""

        self._mode = settings_manager.get("proxy_mode", "static").strip().lower()
        self._init_proxy_pool()
        self._initialized = True

        # 注册配置变更监听
        for key in ["proxy_mode", "proxy_urls", "dynamic_proxy_api_url",
                    "dynamic_proxy_refresh_interval", "dynamic_proxy_jq_path",
                    "dynamic_proxy_protocol", "dynamic_proxy_format",
                    "dynamic_proxy_separator", "dynamic_proxy_username",
                    "dynamic_proxy_password", "dynamic_proxy_batch_mode"]:
            settings_manager.register_watcher(key, self._on_setting_changed)

    # ── 初始化 ─────────────────────────────────────────────

    def _init_proxy_pool(self):
        """根据当前模式初始化代理池"""
        self._mode = settings_manager.get("proxy_mode", "static").strip().lower()
        logger.info("Proxy pool mode: %s", self._mode)

        if self._mode == "dynamic":
            self._load_proxies()  # 也加载静态代理作为兜底
            self._start_dynamic_refresh()
        else:
            self._stop_dynamic_refresh()
            self._load_proxies()

    def _load_proxies(self):
        raw = settings_manager.get("proxy_urls", "").strip()
        if not raw:
            self._proxies = []
            logger.info("Proxy pool: no static proxies configured")
            return

        self._proxies = [p.strip() for p in raw.split(",") if p.strip()]
        logger.info("Proxy pool: loaded %d static proxies", len(self._proxies))

    # ── 动态代理 ─────────────────────────────────────────────

    def _start_dynamic_refresh(self):
        """启动动态代理定时刷新（single 模式下不启动定时器）"""
        self._stop_dynamic_refresh()
        batch_mode = settings_manager.get("dynamic_proxy_batch_mode", "batch").strip().lower()
        if batch_mode == "single":
            logger.info("Dynamic proxy: single mode, scheduled refresh disabled")
            return
        self._refresh_dynamic_proxies()  # 立即刷新一次

    def _stop_dynamic_refresh(self):
        """停止动态代理定时刷新"""
        if self._dynamic_refresh_task:
            self._dynamic_refresh_task.cancel()
            self._dynamic_refresh_task = None
        with self._lock:
            self._dynamic_proxies = []
            self._dynamic_index = 0

    def _schedule_next_refresh(self):
        """调度下一次刷新（single 模式下不调度）"""
        if self._mode != "dynamic":
            return
        batch_mode = settings_manager.get("dynamic_proxy_batch_mode", "batch").strip().lower()
        if batch_mode == "single":
            return
        interval = settings_manager.get_int("dynamic_proxy_refresh_interval", 300)
        self._dynamic_refresh_task = threading.Timer(interval, self._refresh_dynamic_proxies)
        self._dynamic_refresh_task.daemon = True
        self._dynamic_refresh_task.start()

    def _refresh_dynamic_proxies(self):
        """调用供应商API获取新代理列表（batch 模式）"""
        batch_mode = settings_manager.get("dynamic_proxy_batch_mode", "batch").strip().lower()
        if batch_mode == "single":
            return  # single 模式下不缓存批量代理

        import requests

        api_url = settings_manager.get("dynamic_proxy_api_url", "").strip()
        if not api_url:
            self._dynamic_last_error = "未配置动态代理API地址"
            logger.warning("Dynamic proxy: api_url not configured")
            self._schedule_next_refresh()
            return

        try:
            resp = requests.get(api_url, timeout=30)
            resp.raise_for_status()

            fmt = settings_manager.get("dynamic_proxy_format", "json").strip().lower()
            if fmt == "plain_text":
                proxy_list = self._parse_plain_text(resp.text)
            else:
                # 默认 json
                data = resp.json()
                jq_path = settings_manager.get("dynamic_proxy_jq_path", ".data.proxy_list").strip()
                proxy_list = self._extract_by_path(data, jq_path)
                if not isinstance(proxy_list, list):
                    proxy_list = [proxy_list] if proxy_list else []

            # 统一构建带认证信息的代理 URL
            valid_proxies = self._build_proxy_urls(proxy_list)

            interval = settings_manager.get_int("dynamic_proxy_refresh_interval", 300)
            now = time.time()
            with self._lock:
                self._dynamic_proxies = [
                    {"url": p, "expire_at": now + interval}
                    for p in valid_proxies
                ]
                self._dynamic_index = 0
            self._dynamic_last_refresh = now
            self._dynamic_last_error = ""
            logger.info("Dynamic proxy refreshed: %d proxies", len(valid_proxies))

        except Exception as e:
            self._dynamic_last_error = str(e)
            logger.error("Dynamic proxy refresh failed: %s", e)

        self._schedule_next_refresh()

    def _fetch_single_proxy(self) -> Optional[str]:
        """
        实时调用 API 获取一条代理（single 模式）。
        每次调用都会向供应商 API 发起一次新请求，获取到的代理不进入缓存池。
        """
        import requests

        api_url = settings_manager.get("dynamic_proxy_api_url", "").strip()
        if not api_url:
            logger.warning("Single proxy fetch: api_url not configured")
            return None

        try:
            resp = requests.get(api_url, timeout=30)
            resp.raise_for_status()

            fmt = settings_manager.get("dynamic_proxy_format", "json").strip().lower()
            if fmt == "plain_text":
                proxy_list = self._parse_plain_text(resp.text)
            else:
                data = resp.json()
                jq_path = settings_manager.get("dynamic_proxy_jq_path", ".data.proxy_list").strip()
                proxy_list = self._extract_by_path(data, jq_path)
                if not isinstance(proxy_list, list):
                    proxy_list = [proxy_list] if proxy_list else []

            valid_proxies = self._build_proxy_urls(proxy_list)
            if valid_proxies:
                proxy = valid_proxies[0]
                logger.info("Single proxy fetched: %s", proxy)
                return proxy

            logger.warning("Single proxy fetch: no valid proxy in response")
        except Exception as e:
            self._dynamic_last_error = str(e)
            logger.error("Single proxy fetch failed: %s", e)

        return None

    def _parse_plain_text(self, text: str) -> List[str]:
        """根据配置的分隔符解析纯文本响应中的代理列表"""
        sep = settings_manager.get("dynamic_proxy_separator", "newline").strip().lower()
        if sep == "comma":
            parts = text.split(",")
        elif sep == "space":
            parts = text.split()
        elif sep == "semicolon":
            parts = text.split(";")
        else:
            # 默认 newline（也兼容 \r\n 和 \r）
            parts = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        return [p.strip() for p in parts if p.strip()]

    def _build_proxy_urls(self, raw_list: List[Any]) -> List[str]:
        """
        将原始代理条目统一转换为标准代理 URL。
        支持注入用户名密码认证信息。
        """
        protocol = settings_manager.get("dynamic_proxy_protocol", "socks5").strip().lower()
        if protocol not in ("http", "https", "socks5"):
            protocol = "socks5"
        if protocol == "https":
            logger.warning(
                "Dynamic proxy protocol is set to 'https'. "
                "Most proxy providers only support HTTP or SOCKS5. "
                "If you see TLS errors, try switching to 'http' or 'socks5'."
            )

        username = settings_manager.get("dynamic_proxy_username", "").strip()
        password = settings_manager.get("dynamic_proxy_password", "").strip()
        auth = ""
        if username:
            auth = f"{username}:{password}" if password else username

        valid = []
        for item in raw_list:
            url = str(item).strip()
            if not url:
                continue

            # 情况1: 已经是完整 URL（如 http://user:pass@ip:port）
            if url.startswith(("http://", "https://", "socks5://")):
                valid.append(url)
                continue

            # 情况2: 裸 ip:port
            if ":" in url and "@" not in url:
                if auth:
                    url = f"{protocol}://{auth}@{url}"
                else:
                    url = f"{protocol}://{url}"
                valid.append(url)
                continue

            # 其他情况，尝试按协议前缀包装
            if url:
                url = f"{protocol}://{url}" if auth else f"{protocol}://{url}"
                valid.append(url)

        return valid

    @staticmethod
    def _extract_by_path(data: Any, path: str) -> Any:
        """从嵌套字典/列表中按点分隔路径提取值"""
        if not path or path == ".":
            return data
        keys = path.lstrip(".").split(".")
        current = data
        for key in keys:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(key)
            elif isinstance(current, list) and key.isdigit():
                idx = int(key)
                current = current[idx] if 0 <= idx < len(current) else None
            else:
                return None
        return current

    def refresh_dynamic(self) -> dict:
        """手动触发动态代理刷新，返回刷新结果"""
        if self._mode != "dynamic":
            return {"success": False, "error": "当前不是动态代理模式"}

        batch_mode = settings_manager.get("dynamic_proxy_batch_mode", "batch").strip().lower()
        if batch_mode == "single":
            proxy = self._fetch_single_proxy()
            return {
                "success": proxy is not None,
                "count": 1 if proxy else 0,
                "proxy": proxy,
                "last_error": self._dynamic_last_error,
            }

        # 取消现有定时器，立即刷新
        if self._dynamic_refresh_task:
            self._dynamic_refresh_task.cancel()
            self._dynamic_refresh_task = None

        self._refresh_dynamic_proxies()

        with self._lock:
            count = len(self._dynamic_proxies)
        return {
            "success": True,
            "count": count,
            "last_error": self._dynamic_last_error,
        }

    # ── 公共接口 ─────────────────────────────────────────────

    def reload(self):
        """从配置管理器重新加载代理池"""
        with self._lock:
            self._proxies = []
            self._index = 0
            self._fail_until.clear()
            self._dynamic_proxies = []
            self._dynamic_index = 0
        self._init_proxy_pool()

    def _on_setting_changed(self, key: str, value: str):
        """配置变更回调"""
        logger.info("Proxy setting changed: %s", key)
        self.reload()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def enabled(self) -> bool:
        if self._mode == "dynamic":
            batch_mode = settings_manager.get("dynamic_proxy_batch_mode", "batch").strip().lower()
            if batch_mode == "single":
                return bool(settings_manager.get("dynamic_proxy_api_url", "").strip()) or len(self._proxies) > 0
            with self._lock:
                return len(self._dynamic_proxies) > 0 or len(self._proxies) > 0
        return len(self._proxies) > 0

    @property
    def count(self) -> int:
        if self._mode == "dynamic":
            batch_mode = settings_manager.get("dynamic_proxy_batch_mode", "batch").strip().lower()
            if batch_mode == "single":
                return 1 if settings_manager.get("dynamic_proxy_api_url", "").strip() else 0
            with self._lock:
                return len(self._dynamic_proxies)
        return len(self._proxies)

    def next(self) -> Optional[str]:
        """获取下一个可用代理。single 模式下实时调 API 提取，batch 模式下从缓存轮转。"""
        if self._mode == "dynamic":
            batch_mode = settings_manager.get("dynamic_proxy_batch_mode", "batch").strip().lower()
            if batch_mode == "single":
                return self._fetch_single_proxy()
        return self._pick_proxy(rotate=True)

    def get_one(self) -> Optional[str]:
        """获取一个可用代理（不推进轮转索引），用于为公众号分配专属 IP"""
        if self._mode == "dynamic":
            batch_mode = settings_manager.get("dynamic_proxy_batch_mode", "batch").strip().lower()
            if batch_mode == "single":
                return self._fetch_single_proxy()
        return self._pick_proxy(rotate=False)

    def _pick_proxy(self, rotate: bool = True) -> Optional[str]:
        """内部代理选取逻辑"""
        now = time.time()

        # 动态模式：优先使用动态代理
        if self._mode == "dynamic":
            with self._lock:
                alive = [p for p in self._dynamic_proxies if p["expire_at"] > now]
                self._dynamic_proxies = alive
                if alive:
                    proxy = alive[self._dynamic_index % len(alive)]
                    if rotate:
                        self._dynamic_index += 1
                    return proxy["url"]

            # 动态代理全部过期时，回退到静态代理兜底
            logger.debug("Dynamic proxies exhausted, falling back to static")

        # 静态代理逻辑（静态模式 or 动态模式兜底）
        if not self._proxies:
            return None
        with self._lock:
            for _ in range(len(self._proxies)):
                proxy = self._proxies[self._index % len(self._proxies)]
                if rotate:
                    self._index += 1
                if self._fail_until.get(proxy, 0) <= now:
                    return proxy
        return None

    def get_all(self) -> List[str]:
        if self._mode == "dynamic":
            with self._lock:
                return [p["url"] for p in self._dynamic_proxies]
        return list(self._proxies)

    def mark_failed(self, proxy: str):
        """标记代理失败，冷却一段时间后自动恢复"""
        with self._lock:
            self._fail_until[proxy] = time.time() + FAIL_COOLDOWN
        logger.warning("Proxy %s marked failed, cooldown %ds", proxy, FAIL_COOLDOWN)

    def mark_ok(self, proxy: str):
        """标记代理恢复正常"""
        with self._lock:
            self._fail_until.pop(proxy, None)

    def get_status(self) -> dict:
        """返回代理池状态"""
        now = time.time()
        healthy = []
        failed = []
        for p in self._proxies:
            if self._fail_until.get(p, 0) > now:
                failed.append(p)
            else:
                healthy.append(p)

        result = {
            "mode": self._mode,
            "enabled": self.enabled,
            "static": {
                "total": len(self._proxies),
                "healthy": len(healthy),
                "failed": len(failed),
                "failed_proxies": failed,
            },
        }

        if self._mode == "dynamic":
            with self._lock:
                alive = [p for p in self._dynamic_proxies if p["expire_at"] > now]
                expired_count = len(self._dynamic_proxies) - len(alive)
                total = len(self._dynamic_proxies)
            result["dynamic"] = {
                "total": total,
                "alive": len(alive),
                "expired": expired_count,
                "last_refresh": self._dynamic_last_refresh,
                "last_error": self._dynamic_last_error,
            }

        return result


proxy_pool = ProxyPool()
