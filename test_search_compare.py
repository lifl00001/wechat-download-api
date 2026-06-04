"""
投资日报数据源对比测试脚本

用法：
    pip install requests tavily-python
    python test_search_compare.py

需要设置环境变量（或直接填入下方）：
    BAIDU_API_KEY  — 百度千帆 API Key（https://console.bce.baidu.com/qianfan/）
    TAVILY_API_KEY — Tavily API Key（https://app.tavily.com/）
    JINA_API_KEY   — Jina AI API Key（https://jina.ai/reader/，可选）
"""

import requests
import json
import time
import os

# ===== 填入你的 API Key =====
BAIDU_API_KEY = os.environ.get("BAIDU_API_KEY", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
JINA_API_KEY = os.environ.get("JINA_API_KEY", "")

# 测试查询
TEST_QUERIES = [
    "AI 投资 融资 最新动态",
    "机器人 人形机器人 投资 最新",
    "半导体 芯片 投资 融资",
]


def test_baidu_search(query: str):
    """百度搜索 API（千帆 AppBuilder）"""
    if not BAIDU_API_KEY:
        return {"error": "未设置 BAIDU_API_KEY"}

    start = time.time()
    try:
        resp = requests.post(
            "https://qianfan.baidubce.com/v2/ai_search/web_search",
            headers={
                "Authorization": f"Bearer {BAIDU_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "messages": [{"role": "user", "content": query}],
                "search_source": "baidu_search_v2",
                "resource_type_filter": [{"type": "web", "top_k": 10}],
                "search_recency_filter": "week",
            },
            timeout=30,
        )
        elapsed = time.time() - start
        data = resp.json()

        if "code" in data and data["code"] != 200:
            return {"error": f"API错误: {data.get('message', data.get('code', ''))}", "elapsed": elapsed}

        refs = data.get("references", [])
        results = []
        for ref in refs:
            results.append({
                "title": ref.get("title", ""),
                "url": ref.get("url", ""),
                "snippet": (ref.get("content", "") or "")[:200],
                "date": ref.get("date", ""),
                "source": ref.get("website", ref.get("web_anchor", "")),
            })

        return {
            "engine": "百度搜索 (千帆)",
            "count": len(results),
            "elapsed": f"{elapsed:.2f}s",
            "results": results,
        }
    except Exception as e:
        return {"error": str(e), "elapsed": f"{time.time() - start:.2f}s"}


def test_tavily_search(query: str):
    """Tavily Search API（直接 HTTP 调用，无需额外安装包）"""
    if not TAVILY_API_KEY:
        return {"error": "未设置 TAVILY_API_KEY"}

    start = time.time()
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            headers={
                "Content-Type": "application/json",
            },
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "topic": "news",
                "days": 7,
                "max_results": 10,
                "include_raw_content": True,   # ← 拿全文
                "include_answer": False,
            },
            timeout=60,
        )
        elapsed = time.time() - start
        data = resp.json()

        if data.get("error"):
            return {"error": f"API错误: {data['error']}", "elapsed": f"{elapsed:.2f}s"}

        results = []
        for r in data.get("results", []):
            snippet = (r.get("content", "") or "")[:200]
            raw_len = len(r.get("raw_content", "") or "")
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": snippet,
                "raw_content_len": raw_len,  # 全文字数
                "date": r.get("published_date", ""),
                "score": r.get("score", 0),
            })

        return {
            "engine": "Tavily Search",
            "count": len(results),
            "elapsed": f"{elapsed:.2f}s",
            "results": results,
        }
    except Exception as e:
        return {"error": str(e), "elapsed": f"{time.time() - start:.2f}s"}


def test_jina_search(query: str):
    """Jina AI s.jina.ai 搜索"""
    start = time.time()
    try:
        headers = {"Accept": "application/json"}
        if JINA_API_KEY:
            headers["Authorization"] = f"Bearer {JINA_API_KEY}"

        resp = requests.get(
            f"https://s.jina.ai/{query}",
            headers=headers,
            timeout=30,
        )
        elapsed = time.time() - start
        data = resp.json()

        results = []
        for item in data.get("data", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": (item.get("content", "") or "")[:200],
                "date": item.get("date", ""),
            })

        return {
            "engine": "Jina AI Search",
            "count": len(results),
            "elapsed": f"{elapsed:.2f}s",
            "results": results,
        }
    except Exception as e:
        return {"error": str(e), "elapsed": f"{time.time() - start:.2f}s"}


def print_comparison(query: str, baidu_result, tavily_result, jina_result):
    """打印对比结果"""
    print(f"\n{'='*80}")
    print(f"🔍 查询: {query}")
    print(f"{'='*80}")

    engines = [
        ("百度搜索 (千帆)", baidu_result),
        ("Tavily Search", tavily_result),
        ("Jina AI Search", jina_result),
    ]

    # 摘要对比
    print(f"\n{'引擎':<20} {'结果数':<8} {'耗时':<10} {'状态'}")
    print(f"{'-'*20} {'-'*8} {'-'*10} {'-'*10}")
    for name, result in engines:
        if "error" in result:
            print(f"{name:<20} {'—':<8} {result.get('elapsed', '—'):<10} ❌ {result['error']}")
        else:
            print(f"{name:<20} {result['count']:<8} {result['elapsed']:<10} ✅")

    # 详细结果
    for name, result in engines:
        if "error" in result:
            continue
        print(f"\n--- {name} (共 {result['count']} 条) ---")
        for i, r in enumerate(result.get("results", [])[:5], 1):
            print(f"\n  [{i}] {r['title']}")
            print(f"      URL: {r['url']}")
            print(f"      日期: {r.get('date', '无')}")
            if r.get("score"):
                print(f"      相关度: {r['score']}")
            if r.get("raw_content_len"):
                print(f"      全文: {r['raw_content_len']}字")
            snippet = r.get("snippet", "无摘要")
            print(f"      摘要: {snippet[:150]}{'...' if len(snippet) > 150 else ''}")


def main():
    print("=" * 80)
    print("投资日报 — 搜索数据源对比测试")
    print("=" * 80)

    print(f"\nAPI Key 状态:")
    print(f"  百度: {'✅ 已设置' if BAIDU_API_KEY else '❌ 未设置 (BAIDU_API_KEY)'}")
    print(f"  Tavily: {'✅ 已设置' if TAVILY_API_KEY else '❌ 未设置 (TAVILY_API_KEY)'}")
    print(f"  Jina: {'✅ 已设置' if JINA_API_KEY else '⚠️ 未设置 (JINA_API_KEY，不影响使用，但限额低)'}")

    for query in TEST_QUERIES:
        baidu = test_baidu_search(query)
        tavily = test_tavily_search(query)
        jina = test_jina_search(query)
        print_comparison(query, baidu, tavily, jina)

    # 最终对比总结
    print(f"\n{'='*80}")
    print("📊 对比总结")
    print(f"{'='*80}")
    print("""
| 维度           | 百度搜索 (千帆)    | Tavily Search      | Jina AI Search     |
|----------------|-------------------|--------------------|--------------------|
| 免费额度        | 1500次/月          | 1000次/月           | 无Key:20RPM/有Key:500RPM |
| 返回内容        | 标题+URL+摘要片段   | 标题+URL+摘要+评分+全文 | 标题+URL+正文Markdown |
| 有全文？        | ❌ 只有片段          | ✅ 可选(加raw_content)| ✅ 自动返回         |
| News模式       | ❌ 无               | ✅ topic="news"     | ❌ 无               |
| 时效过滤        | ✅ week/month/year  | ✅ days=N           | ❌ 无               |
| 相关度评分      | ❌ 无               | ✅ score 0-1        | ❌ 无               |
| 中文覆盖        | ⭐⭐⭐⭐⭐           | ⭐⭐⭐⭐             | ⭐⭐⭐⭐             |
| 注册难度        | 中（需百度云账号）   | 低（邮箱注册）       | 低（邮箱注册）       |
| 官网           | console.bce.baidu.com | app.tavily.com   | jina.ai/reader      |
    """)


if __name__ == "__main__":
    main()
