#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
邮件通知模块
使用标准库 smtplib 发送告警邮件（凭证失效、服务异常等）。
配置从 .env 读取，无第三方依赖。

环境变量：
    MAIL_HOST   SMTP 主机（如 smtp.163.com）
    MAIL_PORT   SMTP 端口（SSL 默认 465）
    MAIL_SSL    是否启用 SSL（true/false，默认 true）
    MAIL_USER   SMTP 登录用户名
    MAIL_PASS   SMTP 登录密码/授权码
    MAIL_FROM   发件人地址
    MAIL_TO     收件人地址（多个用逗号分隔）
"""

import asyncio
import logging
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate
from typing import Optional

logger = logging.getLogger("email_notifier")

# 节流：同一主题 30 分钟内不重复发送
_NOTIFY_INTERVAL = 30 * 60


class EmailNotifier:
    """邮件通知器（异步包装 smtplib，不阻塞事件循环）"""

    def __init__(self):
        self._last_sent: dict = {}  # subject -> timestamp

    @staticmethod
    def _load_config() -> dict:
        """从 .env 读取邮件配置（每次读取，便于运行中修改）"""
        from pathlib import Path
        from dotenv import dotenv_values

        env_path = Path(__file__).resolve().parent.parent / ".env"
        vals = dotenv_values(env_path) if env_path.exists() else {}
        return {
            "host": vals.get("MAIL_HOST", "").strip(),
            "port": int(vals.get("MAIL_PORT", "465") or "465"),
            "ssl": (vals.get("MAIL_SSL", "true") or "true").strip().lower() == "true",
            "user": vals.get("MAIL_USER", "").strip(),
            "pass": vals.get("MAIL_PASS", "").strip(),
            "from": vals.get("MAIL_FROM", "").strip(),
            "to": vals.get("MAIL_TO", "").strip(),
        }

    @property
    def enabled(self) -> bool:
        """是否配置完整（缺任一项视为未启用）"""
        c = self._load_config()
        return all([c["host"], c["port"], c["user"], c["pass"], c["from"], c["to"]])

    def _send_sync(self, subject: str, html_body: str, to_addrs: list) -> None:
        """同步发送（在线程池中执行）"""
        c = self._load_config()
        msg = MIMEMultipart("alternative")
        msg["From"] = formataddr(("WeChat API Monitor", c["from"]))
        msg["To"] = ", ".join(to_addrs)
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        # 纯文本兜底
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        if c["ssl"]:
            with smtplib.SMTP_SSL(c["host"], c["port"], timeout=20) as smtp:
                smtp.login(c["user"], c["pass"])
                smtp.sendmail(c["from"], to_addrs, msg.as_string())
        else:
            with smtplib.SMTP(c["host"], c["port"], timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(c["user"], c["pass"])
                smtp.sendmail(c["from"], to_addrs, msg.as_string())

    async def notify(self, subject: str, html_body: str, force: bool = False) -> bool:
        """
        发送邮件通知（带节流）。

        Args:
            subject: 邮件主题
            html_body: HTML 正文
            force: 强制发送（忽略节流）

        Returns:
            True 发送成功 / False 失败或被节流
        """
        if not self.enabled:
            logger.debug("邮件通知未启用（MAIL_* 配置不完整），跳过: %s", subject)
            return False

        # 节流检查
        now = time.time()
        last = self._last_sent.get(subject, 0)
        if not force and now - last < _NOTIFY_INTERVAL:
            logger.debug("邮件节流跳过: %s（距上次 %ds）", subject, int(now - last))
            return False

        c = self._load_config()
        to_addrs = [a.strip() for a in c["to"].split(",") if a.strip()]
        if not to_addrs:
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._send_sync, subject, html_body, to_addrs)
            self._last_sent[subject] = now
            logger.info("邮件已发送: %s -> %s", subject, to_addrs)
            return True
        except Exception as e:
            logger.error("邮件发送失败: %s - %s", subject, e)
            return False


# 全局单例
email_notifier = EmailNotifier()
