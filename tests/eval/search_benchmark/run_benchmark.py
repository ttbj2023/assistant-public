"""搜索四路对比 benchmark: 豆包 / 智谱 / 百度 / Gemini Grounding.

绕过项目初始化, 直接调用各路 API. 评估个人助手信息查询场景下,
豆包(字节系信源)相对其余三路的差异化价值, 据此判断是否值得引入.

调用方式:
- 豆包: Custom版 REST (feedcoopapi), 用 ARK_AGENT_PLAN_API_KEY 鉴权
- 智谱: /paas/v4/web_search REST (search_std)
- 百度: AppBuilder AI搜索 MCP SSE (无裸搜索 REST, 只能走 MCP)
- Gemini: google-genai SDK + google_search tool (经 GEMINI_BASE_URL 中转)

环境变量(.env): ARK_AGENT_PLAN_API_KEY, ZHIPU_API_KEY, BAIDU_API_KEY,
               GEMINI_API_KEY, GEMINI_BASE_URL

用法:
    python tests/eval/search_benchmark/run_benchmark.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("benchmark")

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# 字节系信源域名 / 站点名 (用于验证豆包是否真用上独家信源)
BYTEDANCE_DOMAINS = (
    "toutiao.com",
    "douyin.com",
    "ixigua.com",
    "fanqienovel.com",
    "fanqie.com",
    "bytedance.com",
    "dcdapp.com",
)
BYTEDANCE_SITE_NAMES = ("今日头条", "抖音", "西瓜视频", "番茄")

GEMINI_MODEL = "gemini-2.5-flash-lite"
SNIPPET_LEN = 600
ANSWER_LEN = 1500

# 14 条 query: 覆盖个人助手场景, 已剔除 geo 工具能覆盖的 POI/路线类
TEST_QUERIES: list[dict[str, str]] = [
    {"id": "01_news", "category": "时事热点", "query": "2026年6月最近的人工智能行业大事件", "expect": "doubao"},
    {"id": "02_drama", "category": "影视娱乐", "query": "最近有什么好看的新电视剧 2026年6月", "expect": "doubao"},
    {"id": "03_recipe", "category": "生活菜谱", "query": "空气炸锅简单又好吃的食谱推荐", "expect": "doubao"},
    {"id": "04_travel", "category": "旅游攻略", "query": "三亚亲子自由行攻略", "expect": "doubao"},
    {"id": "05_meme", "category": "网络热梗", "query": "最近网上流行的梗有哪些", "expect": "doubao"},
    {"id": "06_phone", "category": "数码评测", "query": "iPhone 17 真实用户评价 缺点", "expect": "doubao"},
    {"id": "07_langchain", "category": "英文技术", "query": "LangChain v1.0 Agent 如何用 ContextVar 透传上下文", "expect": "zhipu/gemini"},
    {"id": "08_asyncio", "category": "编程", "query": "Python asyncio TaskGroup 和 gather 区别", "expect": "zhipu/gemini"},
    {"id": "09_rag", "category": "学术", "query": "大模型 RAG 最新研究进展 2026", "expect": "gemini/zhipu"},
    {"id": "10_medical", "category": "医学专业", "query": "二甲双胍副作用和禁忌症", "expect": "baidu/gemini"},
    {"id": "11_uspolicy", "category": "国际新闻", "query": "美国最新AI监管政策动态", "expect": "gemini"},
    {"id": "12_python", "category": "官方规范", "query": "Python 3.13 free-threading GIL 新特性", "expect": "gemini"},
    {"id": "13_person", "category": "人物百科", "query": "黄仁勋个人经历与英伟达发展", "expect": "neutral"},
    {"id": "14_phone_buy", "category": "购物决策", "query": "2026年千元机性价比推荐", "expect": "neutral"},
]

ROUTES = ("doubao", "zhipu", "baidu", "gemini")


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _is_bytedance(url: str, site: str = "") -> bool:
    host = (urlparse(url).hostname or "").lower()
    if any(d in host for d in BYTEDANCE_DOMAINS):
        return True
    site_l = (site or "").lower()
    return any(name in site for name in BYTEDANCE_SITE_NAMES) or any(
        name in site_l for name in BYTEDANCE_SITE_NAMES
    )


def _truncate(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "..."


async def search_doubao(query: str) -> dict[str, Any]:
    """豆包 Custom版 REST."""
    url = "https://open.feedcoopapi.com/search_api/web_search"
    headers = {
        "Authorization": f"Bearer {os.environ['ARK_AGENT_PLAN_API_KEY']}",
        "Content-Type": "application/json",
    }
    payload = {
        "Query": query,
        "SearchType": "web",
        "Count": 5,
        "NeedSummary": True,
        "Filter": {"NeedUrl": True},
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()

    err = (data.get("ResponseMetadata") or {}).get("Error")
    if err:
        return {"error": json.dumps(err, ensure_ascii=False), "results": []}

    items: list[dict[str, Any]] = []
    for w in (data.get("Result") or {}).get("WebResults") or []:
        u = w.get("Url") or ""
        site = w.get("SiteName") or ""
        items.append({
            "title": w.get("Title", ""),
            "url": u,
            "domain": _domain(u),
            "site": site,
            "snippet": _truncate(w.get("Summary") or w.get("Snippet") or "", SNIPPET_LEN),
            "publish_time": w.get("PublishTime", ""),
            "auth_info": w.get("AuthInfoDes", ""),
            "rank_score": w.get("RankScore"),
            "is_bytedance": _is_bytedance(u, site),
        })
    return {"count": len(items), "results": items}


async def search_zhipu(query: str) -> dict[str, Any]:
    """智谱 web_search REST (search_std)."""
    url = "https://open.bigmodel.cn/api/paas/v4/web_search"
    headers = {
        "Authorization": f"Bearer {os.environ['ZHIPU_API_KEY']}",
        "Content-Type": "application/json",
    }
    payload = {
        "search_query": query[:70],
        "search_engine": "search_std",
        "search_intent": False,
        "count": 5,
        "content_size": "high",
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()

    items: list[dict[str, Any]] = []
    for w in data.get("search_result") or []:
        u = w.get("link") or ""
        items.append({
            "title": w.get("title", ""),
            "url": u,
            "domain": _domain(u),
            "site": w.get("refer", ""),
            "snippet": _truncate(w.get("content") or "", SNIPPET_LEN),
            "publish_time": w.get("publish_date", ""),
            "is_bytedance": _is_bytedance(u, w.get("refer", "")),
        })
    return {"count": len(items), "results": items}


def _parse_baidu_text(text: str) -> list[dict[str, Any]]:
    """解析百度 AIsearch 返回文本 (Title/Content/URL 块)."""
    items: list[dict[str, Any]] = []
    blocks = re.split(r"(?=Title:)", text)
    for b in blocks:
        b = b.strip()
        if not b.startswith("Title:"):
            continue
        m_title = re.search(r"Title:\s*(.*?)(?:\n|$)", b)
        m_url = re.search(r"URL:\s*(\S+)", b)
        m_content = re.search(r"Content:\s*([\s\S]*?)(?=URL:|$)", b)
        u = (m_url.group(1).strip() if m_url else "")
        items.append({
            "title": (m_title.group(1).strip() if m_title else ""),
            "url": u,
            "domain": _domain(u),
            "snippet": _truncate((m_content.group(1) if m_content else "").strip(), SNIPPET_LEN),
            "is_bytedance": _is_bytedance(u),
        })
    return items


async def search_baidu(query: str) -> dict[str, Any]:
    """百度 AI搜索 (MCP SSE, 无裸搜索 REST)."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    key = os.environ["BAIDU_API_KEY"]
    sse_url = f"http://appbuilder.baidu.com/v2/ai_search/mcp/sse?api_key={key}"
    async with sse_client(sse_url) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            res = await s.call_tool("AIsearch", {"query": query})

    text = "\n".join(getattr(c, "text", "") for c in res.content)
    items = _parse_baidu_text(text)
    # 若未解析出离散块, 保留全文作为 answer 供人工查阅
    return {
        "count": len(items),
        "results": items,
        "answer": _truncate(text, ANSWER_LEN) if not items else "",
    }


async def search_gemini(query: str) -> dict[str, Any]:
    """Gemini Grounding (google_search tool)."""
    from google import genai
    from google.genai import types

    api_key = os.environ["GEMINI_API_KEY"]
    kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.environ.get("GEMINI_BASE_URL", "")
    if base_url:
        kwargs["http_options"] = types.HttpOptions(base_url=base_url)
    client = genai.Client(**kwargs)

    # 中转节点不稳定(503/429), 加超时 + 1 次重试
    contents = f"{query}\n\n请用中文回答, 并在末尾列出参考来源."
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())]
    )
    resp = None
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=GEMINI_MODEL, contents=contents, config=config
                ),
                timeout=70,
            )
            break
        except Exception as e:
            last_err = e
            if attempt == 0:
                await asyncio.sleep(2)
    if resp is None:
        raise RuntimeError(f"gemini 调用失败: {last_err}")

    answer = (getattr(resp, "text", "") or "").strip()
    sources: list[dict[str, str]] = []
    try:
        cand = resp.candidates[0]
        gm = getattr(cand, "grounding_metadata", None)
        if gm and getattr(gm, "grounding_chunks", None):
            for ch in gm.grounding_chunks:
                web = getattr(ch, "web", None)
                if web and getattr(web, "uri", None):
                    sources.append({"title": getattr(web, "title", "") or "", "url": web.uri})
    except Exception as e:
        logger.warning("gemini grounding 解析失败: %s", e)

    items = [
        {
            "title": s["title"],
            "url": s["url"],
            "domain": _domain(s["url"]),
            "snippet": "",
            "is_bytedance": _is_bytedance(s["url"]),
        }
        for s in sources
    ]
    return {"count": len(items), "results": items, "answer": _truncate(answer, ANSWER_LEN)}


async def run_query(q: dict[str, str]) -> dict[str, Any]:
    """对单条 query 并发跑 4 路."""
    record: dict[str, Any] = {
        "query": q["query"],
        "category": q["category"],
        "expect": q["expect"],
    }

    async def _run(name: str, fn: Any) -> tuple[str, dict[str, Any]]:
        t0 = time.time()
        try:
            out = await fn(q["query"])
            out["elapsed"] = round(time.time() - t0, 2)
        except Exception as e:
            out = {"error": f"{type(e).__name__}: {e}", "results": [], "elapsed": round(time.time() - t0, 2)}
        return name, out

    fns = {
        "doubao": search_doubao,
        "zhipu": search_zhipu,
        "baidu": search_baidu,
        "gemini": search_gemini,
    }
    pairs = await asyncio.gather(*[_run(n, f) for n, f in fns.items()])
    for name, out in pairs:
        record[name] = out
    return record


def _line(name: str, rec: dict[str, Any]) -> str:
    """格式化单路单行摘要."""
    if "error" in rec:
        return f"  {name:7} ERROR: {rec['error'][:80]} ({rec.get('elapsed', '?')}s)"
    results = rec.get("results", [])
    byted = sum(1 for r in results if r.get("is_bytedance"))
    domains = [r.get("domain") or "?" for r in results[:3]]
    ans = " [有总结]" if rec.get("answer") else ""
    return (
        f"  {name:7} {len(results)}条 字节系:{byted} {rec['elapsed']}s{ans}\n"
        f"          domains: {domains}"
    )


def main() -> None:
    for k in ("ARK_AGENT_PLAN_API_KEY", "ZHIPU_API_KEY", "BAIDU_API_KEY", "GEMINI_API_KEY"):
        if not os.environ.get(k):
            print(f"缺少环境变量 {k}")
            sys.exit(1)

    all_records: dict[str, Any] = {}
    print(f"=== 搜索四路 benchmark ({len(TEST_QUERIES)} queries x {len(ROUTES)} routes) ===\n")

    for q in TEST_QUERIES:
        out_file = RESULTS_DIR / f"{q['id']}.json"
        if out_file.exists():
            all_records[q["id"]] = json.loads(out_file.read_text())
            print(f"[{q['id']}] 已存在, 跳过")
            continue
        rec = asyncio.run(run_query(q))
        all_records[q["id"]] = rec
        out_file.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
        print(f"[{q['id']} {q['category']}] {q['query']}  (预期占优: {q['expect']})", flush=True)
        for name in ROUTES:
            print(_line(name, rec.get(name, {})), flush=True)
        print(flush=True)

    (RESULTS_DIR / "all.json").write_text(
        json.dumps(all_records, ensure_ascii=False, indent=2)
    )
    _print_summary(all_records)


def _print_summary(records: dict[str, Any]) -> None:
    """汇总统计: 各路成功率 / 平均召回数 / 字节系信源命中."""
    print("\n=== 汇总 ===")
    header = f"{'route':8} {'成功':>4} {'平均召回':>8} {'字节系命中':>10} {'有总结':>6}"
    print(header)
    for name in ROUTES:
        ok = 0
        counts = []
        byted_total = 0
        answer_total = 0
        for rec in records.values():
            r = rec.get(name, {})
            if "error" in r:
                continue
            ok += 1
            counts.append(len(r.get("results", [])))
            byted_total += sum(1 for x in r.get("results", []) if x.get("is_bytedance"))
            if r.get("answer"):
                answer_total += 1
        avg = f"{sum(counts) / len(counts):.1f}" if counts else "-"
        print(f"{name:8} {ok:>4} {avg:>8} {byted_total:>10} {answer_total:>6}")
    print(f"\n详细结果: {RESULTS_DIR}/all.json")


if __name__ == "__main__":
    main()
