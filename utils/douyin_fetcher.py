#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音创作者中心数据采集器
通过 CDP（Chrome DevTools Protocol）控制 Chrome，抓取抖音创作者中心的热门话题

source_engine 标记为 douyin_creator，写入 news_items 表

前提：Chrome 以调试模式运行（--remote-debugging-port=9222），且已登录抖音创作者中心
"""

import json
import logging
import os
import time
from typing import List, Dict

import requests

logger = logging.getLogger(__name__)

_CDP_BASE = os.getenv("CDP_BASE_URL", "http://localhost:9222")
_CDP_TIMEOUT = 15
_DOUYIN_CREATOR_URL = "https://creator.douyin.com/creator-micro/home"


def fetch_douyin_hot_topics(max_results: int = 15) -> List[Dict]:
    """
    通过 CDP 抓取抖音创作者中心的热门话题（"猜你喜欢"推荐话题）

    Returns:
        标准化的新闻条目列表，source_engine = "douyin_creator"
    """
    try:
        # 1. 优先复用已有的抖音标签页（页面已加载完），没有才创建新的
        tab_id = _find_or_create_douyin_tab()
        if not tab_id:
            logger.warning("无法获取抖音标签页，Chrome CDP 可能未启动")
            return []

        # 2. 通过 WebSocket 执行 JS 提取热门话题
        # 页面结构：序号→标题→"热度"→数值（如 2.7万）
        js_code = """
        (function() {
            var text = document.body.innerText;
            var topics = [];
            var lines = text.split('\\n');
            for (var i = 0; i < lines.length; i++) {
                if (lines[i].trim() === '热度') {
                    // 标题在"热度"上一行（lines[i-1]），热度数值在下一行（lines[i+1]）
                    var topic = lines[i-1] || '';
                    var heat = (lines[i+1] || '').replace(/[^\\d.百万]/g, '');
                    // 跳过纯数字的行（那可能是序号不是标题）
                    if (topic && heat && !/^\\d+$/.test(topic.trim())) {
                        topics.push({title: topic.trim(), heat: heat.trim()});
                    }
                }
            }
            return JSON.stringify(topics.slice(0, %d));
        })()
        """ % max_results

        result = _eval_in_tab(tab_id, js_code)
        # 复用模式下不关闭标签页

        if not result:
            logger.info("douyin_creator: 未抓到热门话题")
            return []

        topics = json.loads(result)
        items = []
        for topic in topics:
            title = topic.get("title", "")
            heat = topic.get("heat", "")
            if not title:
                continue
            items.append({
                "title": title,
                "url": "",  # 抖音热门话题不一定有独立URL
                "snippet": f"热度: {heat}" if heat else "",
                "full_text": "",
                "source_engine": "douyin_creator",
                "source_name": "douyin-creator",
                "published_date": "",
                "author": "douyin",
                "score": _parse_heat(heat),
            })

        logger.info("douyin_creator: %d hot topics", len(items))
        return items

    except requests.exceptions.ConnectionError:
        logger.warning("Chrome CDP 未启动（%s），跳过抖音采集", _CDP_BASE)
        return []
    except Exception as e:
        logger.error("douyin_creator error: %s", e)
        return []


def fetch_douyin_video_stats() -> List[Dict]:
    """
    通过 CDP 抓取抖音最新视频的数据（播放量/点赞/评论）
    这是内容复盘用的，也写入 news_items 表（source_engine = douyin_stats）

    注意：这个数据不是"新闻"，而是"自己的内容表现数据"。
    存入 news_items 表是为了统一管理，用 source_engine 区分。
    """
    try:
        target = _create_tab("https://creator.douyin.com/creator-micro/home")
        if not target:
            return []

        tab_id = target.get("id")
        time.sleep(8)

        js_code = """
        (function() {
            var text = document.body.innerText;
            var data = {raw: text.slice(0, 3000)};

            var playMatch = text.match(/播放量[\\s\\n]*(\\d+)/);
            if (playMatch) data.plays = parseInt(playMatch[1]);

            var likeMatch = text.match(/点赞量[\\s\\n]*(\\d+)/);
            if (likeMatch) data.likes = parseInt(likeMatch[1]);

            var commentMatch = text.match(/评论量[\\s\\n]*(\\d+)/);
            if (commentMatch) data.comments = parseInt(commentMatch[1]);

            var shareMatch = text.match(/分享量[\\s\\n]*(\\d+)/);
            if (shareMatch) data.shares = parseInt(shareMatch[1]);

            var fansMatch = text.match(/粉丝[\\s\\n]*(\\d+)/);
            if (fansMatch) data.followers = parseInt(fansMatch[1]);

            return JSON.stringify(data);
        })()
        """

        result = _eval_in_tab(tab_id, js_code)
        _close_tab(tab_id)

        if not result:
            return []

        data = json.loads(result)
        # 把视频数据包装成"新闻条目"格式存入数据库
        if data.get("plays") is not None:
            return [{
                "title": f"[抖音数据] 播放{data.get('plays',0)} 赞{data.get('likes',0)} 评{data.get('comments',0)}",
                "url": "",
                "snippet": json.dumps(data, ensure_ascii=False),
                "full_text": json.dumps(data, ensure_ascii=False),
                "source_engine": "douyin_stats",
                "source_name": "douyin-stats",
                "published_date": time.strftime("%Y-%m-%d"),
                "author": "douyin",
                "score": float(data.get("plays", 0)),
            }]
        return []

    except Exception as e:
        logger.error("douyin_stats error: %s", e)
        return []


# ── CDP 工具函数 ──────────────────────────────────────────────

def _find_or_create_douyin_tab() -> str:
    """
    优先复用已有的抖音标签页（页面已加载完）。
    没有则创建新标签页并等待加载。
    返回 tabId 或空字符串。
    """
    try:
        resp = requests.get(f"{_CDP_BASE}/json/list", timeout=5)
        targets = resp.json()
        # 找已有的抖音创作者中心标签页
        for t in targets:
            if (t.get("type") == "page" and
                    "creator.douyin.com" in t.get("url", "")):
                logger.info("复用已有抖音标签页: %s", t["id"])
                return t["id"]
    except Exception:
        pass

    # 没有则创建新标签页（等待更长时间确保加载完）
    target = _create_tab(_DOUYIN_CREATOR_URL)
    if target:
        tab_id = target.get("id", "")
        if tab_id:
            time.sleep(12)  # 新标签页需要更长加载时间
        return tab_id
    return ""


def _create_tab(url: str) -> Dict:
    """通过 CDP 创建新标签页"""
    try:
        resp = requests.put(
            f"{_CDP_BASE}/json/new",
            params={"url": url},
            timeout=_CDP_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.debug("create_tab error: %s", e)
    return {}


def _close_tab(tab_id: str):
    """关闭标签页"""
    try:
        requests.get(f"{_CDP_BASE}/json/close/{tab_id}", timeout=5)
    except Exception:
        pass


def _eval_in_tab(tab_id: str, expression: str) -> str:
    """
    通过 WebSocket 在指定标签页执行 JS，返回结果
    使用 websocket-client 库（如果可用），否则用原始 socket
    """
    # 获取标签页的 WebSocket 地址
    try:
        resp = requests.get(f"{_CDP_BASE}/json/list", timeout=5)
        targets = resp.json()
        target = next((t for t in targets if t.get("id") == tab_id), None)
        if not target:
            return ""
        ws_url = target.get("webSocketDebuggerUrl", "")
        if not ws_url:
            return ""
    except Exception:
        return ""

    # 尝试用 websocket-client
    try:
        import websocket
        ws = websocket.create_connection(ws_url, timeout=_CDP_TIMEOUT)
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True}
        }))
        resp = json.loads(ws.recv())
        ws.close()
        if "result" in resp and "result" in resp["result"]:
            return resp["result"]["result"].get("value", "")
    except ImportError:
        logger.warning("websocket-client 未安装，无法执行 CDP JS。请运行: pip install websocket-client")
    except Exception as e:
        logger.debug("eval_in_tab error: %s", e)

    return ""


def _parse_heat(heat_str) -> float:
    """解析热度字符串为数字"""
    if not heat_str:
        return 0.0
    try:
        s = str(heat_str).strip()
        if "万" in s:
            return float(s.replace("万", "")) * 10000
        if "亿" in s:
            return float(s.replace("亿", "")) * 100000000
        return float(s)
    except (ValueError, TypeError):
        return 0.0
