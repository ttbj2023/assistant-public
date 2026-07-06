"""Gemma 模型 Grounding 能力验证.

验证 Gemma 4 (26b/31b) 是否真正支持三种 Google 接地能力:
- Search Grounding (google_search)
- Maps Grounding (google_maps)
- URL Context (url_context) 两条路径:
  A) Interactions API (client.interactions.create) — 现有生产实现
  B) generateContent + url_context tool — 统一路径候选(决定阶段二是否重构)

核心判据: 不只看调用是否报错, 必须确认返回的 grounding/url_context 元数据非空,
否则工具虽被"接受"却未真正触发检索, 属于"假装接地".

对照基线:
- search: gemini-2.5-flash-lite
- maps / url_context: gemini-3.1-flash-lite-preview

环境变量: GEMINI_API_KEY, GEMINI_BASE_URL (中转节点, 自动从项目根 .env 加载)

用法:
    python tests/eval/gemma_grounding_eval.py
    python tests/eval/gemma_grounding_eval.py --baseline
    python tests/eval/gemma_grounding_eval.py --models gemma-4-26b-a4b-it --capabilities search maps
    python tests/eval/gemma_grounding_eval.py --save --raw
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("需要安装: pip install google-genai python-dotenv")
    sys.exit(1)

# 自动加载项目根 .env (含 GEMINI_API_KEY / GEMINI_BASE_URL)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

OUTPUT_DIR = Path(__file__).parent / "gemma_grounding_results"

DEFAULT_MODELS = ["gemma-4-26b-a4b-it", "gemma-4-31b-it"]
# 各能力的 Gemini 基线模型, --baseline 时追加
BASELINES = {
    "search": ["gemini-2.5-flash-lite"],
    "maps": ["gemini-3.1-flash-lite-preview"],
    "search_interactions": ["gemini-3.1-flash-lite-preview"],
    "maps_interactions": ["gemini-3.1-flash-lite-preview"],
    "url_interactions": ["gemini-3.1-flash-lite-preview"],
    "url_gencontent": ["gemini-3.1-flash-lite-preview"],
}
ALL_CAPABILITIES = [
    "search",
    "maps",
    "search_interactions",
    "maps_interactions",
    "url_interactions",
    "url_gencontent",
]

# ── 测试用例 ──────────────────────────────────────────────────────────
# search: 时效性问题, 必须真正联网才能答准, 便于判断 grounding 真实性
SEARCH_QUERIES = [
    {
        "id": "s1",
        "query": "2026年人工智能领域有哪些重大突破? 列举最新的模型或技术.",
        "note": "时效性搜索",
    },
    {
        "id": "s2",
        "query": "Python 3.13 引入了哪些主要新特性?",
        "note": "技术事实搜索",
    },
]

# maps: 一个带坐标(POI推荐), 一个纯地理知识
MAPS_QUERIES = [
    {
        "id": "m1",
        "query": "北京南站附近有什么好吃的餐厅推荐? 要评分高的",
        "lat": 39.865,
        "lng": 116.378,
        "note": "POI 推荐 + 坐标",
    },
    {
        "id": "m2",
        "query": "杭州西湖在哪个区? 面积多大? 有什么著名景点?",
        "lat": None,
        "lng": None,
        "note": "地理知识问答",
    },
]

# url_context: 使用稳定可访问的页面
URL_CONTEXT_QUERIES = [
    {
        "id": "u1",
        "query": "总结这个页面的核心内容, 这个技术的主要特点是什么?",
        "urls": ["https://en.wikipedia.org/wiki/Large_language_model"],
        "note": "维基百科稳定页面",
    },
]


def create_client() -> genai.Client:
    """创建 Gemini 客户端, 复用 maps eval 的初始化逻辑."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    base_url = os.environ.get("GEMINI_BASE_URL", "")
    if not api_key:
        print("GEMINI_API_KEY 未设置 (检查 .env)")
        sys.exit(1)
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["http_options"] = types.HttpOptions(base_url=base_url)
    return genai.Client(**client_kwargs)


# ── 标准化结果构造 ────────────────────────────────────────────────────
def _fail(error: Exception, elapsed: float) -> dict[str, Any]:
    return {
        "success": False,
        "error": str(error),
        "error_type": type(error).__name__,
        "metadata_real": False,
        "elapsed": round(elapsed, 2),
    }


def _ok(extra: dict[str, Any], elapsed: float) -> dict[str, Any]:
    extra.update({"success": True, "elapsed": round(elapsed, 2)})
    return extra


# ── 能力测试函数 ──────────────────────────────────────────────────────
def test_search(
    client: genai.Client, model: str, query: str
) -> dict[str, Any]:
    """Search Grounding: generateContent + google_search tool."""
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )
    prompt = f"{query}\n\n请用中文回答."
    start = time.time()
    try:
        response = client.models.generate_content(
            model=model, contents=prompt, config=config
        )
        elapsed = time.time() - start
        text = response.text or ""
        chunks = 0
        queries: list[str] = []
        if response.candidates:
            gm = response.candidates[0].grounding_metadata
            if gm:
                chunks = len(gm.grounding_chunks or [])
                queries = list(gm.web_search_queries or [])
        return _ok(
            {
                "answer": text,
                "grounding_chunks": chunks,
                "search_queries": queries,
                "metadata_real": chunks > 0 or len(queries) > 0,
                "raw": _dump(response),
            },
            elapsed,
        )
    except Exception as e:
        return _fail(e, time.time() - start)


def test_maps(
    client: genai.Client,
    model: str,
    query: str,
    lat: float | None,
    lng: float | None,
) -> dict[str, Any]:
    """Maps Grounding: generateContent + google_maps tool (+可选位置个性化)."""
    config_kwargs: dict[str, Any] = {
        "tools": [types.Tool(google_maps=types.GoogleMaps())],
    }
    if lat is not None and lng is not None:
        config_kwargs["tool_config"] = types.ToolConfig(
            retrieval_config=types.RetrievalConfig(
                lat_lng=types.LatLng(latitude=lat, longitude=lng)
            )
        )
    config = types.GenerateContentConfig(**config_kwargs)
    prompt = f"{query}\n\n请用中文回答."
    start = time.time()
    try:
        response = client.models.generate_content(
            model=model, contents=prompt, config=config
        )
        elapsed = time.time() - start
        text = response.text or ""
        maps_chunks = 0
        sources: list[str] = []
        queries: list[str] = []
        if response.candidates:
            gm = response.candidates[0].grounding_metadata
            if gm:
                queries = list(gm.web_search_queries or [])
                for chunk in gm.grounding_chunks or []:
                    if chunk.maps:
                        maps_chunks += 1
                        sources.append(chunk.maps.title or chunk.maps.uri or "")
        return _ok(
            {
                "answer": text,
                "maps_chunks": maps_chunks,
                "maps_sources": sources,
                "search_queries": queries,
                "metadata_real": maps_chunks > 0,
                "raw": _dump(response),
            },
            elapsed,
        )
    except Exception as e:
        return _fail(e, time.time() - start)


def test_search_interactions(
    client: genai.Client, model: str, query: str
) -> dict[str, Any]:
    """Search via Interactions API (client.interactions.create + google_search)."""
    start = time.time()
    try:
        interaction = client.interactions.create(
            api_version="v1beta",
            model=model,
            input=f"{query}\n\n请用中文回答.",
            tools=[{"type": "google_search"}],
        )
        elapsed = time.time() - start
        citations = _collect_annotations(interaction, "url_citation")
        text = getattr(interaction, "output_text", "") or ""
        return _ok(
            {
                "answer": text,
                "citations": len(citations),
                "metadata_real": len(citations) > 0,
                "raw": _dump(interaction),
            },
            elapsed,
        )
    except Exception as e:
        return _fail(e, time.time() - start)


def test_maps_interactions(
    client: genai.Client,
    model: str,
    query: str,
    lat: float | None,
    lng: float | None,
) -> dict[str, Any]:
    """Maps via Interactions API (tool 内带 latitude/longitude)."""
    tool: dict[str, Any] = {"type": "google_maps"}
    if lat is not None and lng is not None:
        tool["latitude"] = lat
        tool["longitude"] = lng
    start = time.time()
    try:
        interaction = client.interactions.create(
            api_version="v1beta",
            model=model,
            input=f"{query}\n\n请用中文回答.",
            tools=[tool],
        )
        elapsed = time.time() - start
        places = _collect_annotations(interaction, "place_citation")
        text = getattr(interaction, "output_text", "") or ""
        return _ok(
            {
                "answer": text,
                "places": len(places),
                "metadata_real": len(places) > 0,
                "raw": _dump(interaction),
            },
            elapsed,
        )
    except Exception as e:
        return _fail(e, time.time() - start)


def test_url_interactions(
    client: genai.Client, model: str, query: str, urls: list[str]
) -> dict[str, Any]:
    """URL Context 路径A: Interactions API (现有生产实现)."""
    start = time.time()
    try:
        interaction = client.interactions.create(
            api_version="v1beta",
            model=model,
            input=_build_url_prompt(query, urls),
            tools=[{"type": "url_context"}],
        )
        elapsed = time.time() - start
        text_parts: list[str] = []
        citations = 0
        retrievals: list[dict[str, str]] = []
        for step in _gv(interaction, "steps", []) or []:
            step_type = _gv(step, "type", "")
            if step_type == "model_output":
                for block in _gv(step, "content", []) or []:
                    if _gv(block, "type", "") != "text":
                        continue
                    t = _gv(block, "text", "")
                    if t:
                        text_parts.append(str(t))
                    citations += _count_url_citations(block)
            elif step_type == "url_context_result":
                retrievals.extend(_extract_retrievals(step))
        answer = "\n".join(text_parts).strip()
        retrieval_ok = any(r["status"] == "success" for r in retrievals)
        return _ok(
            {
                "answer": answer,
                "citations": citations,
                "retrievals": retrievals,
                "metadata_real": citations > 0 and retrieval_ok,
                "raw": _dump(interaction),
            },
            elapsed,
        )
    except Exception as e:
        return _fail(e, time.time() - start)


def test_url_gencontent(
    client: genai.Client, model: str, query: str, urls: list[str]
) -> dict[str, Any]:
    """URL Context 路径B: generateContent + url_context tool (统一路径候选)."""
    config = types.GenerateContentConfig(
        tools=[types.Tool(url_context=types.UrlContext())],
    )
    prompt = _build_url_prompt(query, urls)
    start = time.time()
    try:
        response = client.models.generate_content(
            model=model, contents=prompt, config=config
        )
        elapsed = time.time() - start
        text = response.text or ""
        url_meta: list[dict[str, str]] = []
        citations = 0
        if response.candidates:
            cand = response.candidates[0]
            ucm = getattr(cand, "url_context_metadata", None)
            if ucm and getattr(ucm, "url_metadata", None):
                for um in ucm.url_metadata:
                    url_meta.append(
                        {
                            "url": getattr(um, "retrieved_url", "") or "",
                            "status": str(
                                getattr(um, "url_retrieval_status", "")
                            ),
                        }
                    )
            cm = getattr(cand, "citation_metadata", None)
            if cm and getattr(cm, "citation_sources", None):
                citations = len(cm.citation_sources)
        retrieval_ok = any(m["status"] == "SUCCESS" for m in url_meta)
        return _ok(
            {
                "answer": text,
                "url_metadata": url_meta,
                "citations": citations,
                "metadata_real": len(url_meta) > 0 or citations > 0,
                "retrieval_ok": retrieval_ok,
                "raw": _dump(response),
            },
            elapsed,
        )
    except Exception as e:
        return _fail(e, time.time() - start)


# ── 解析辅助 ──────────────────────────────────────────────────────────
def _dump(obj: Any) -> Any:
    """pydantic 模型转 dict (供 --raw 保存), 失败则返回 None."""
    try:
        return obj.model_dump(exclude_none=True)
    except Exception:
        return None


def _collect_annotations(interaction: Any, ann_type: str) -> list[str]:
    """从 Interactions 响应的 model_output step 收集指定类型 annotation."""
    out: list[str] = []
    for step in getattr(interaction, "steps", None) or []:
        if _gv(step, "type", "") != "model_output":
            continue
        for block in _gv(step, "content", []) or []:
            for ann in _gv(block, "annotations", []) or []:
                if _gv(ann, "type", "") == ann_type:
                    out.append(
                        _gv(ann, "url", "")
                        or _gv(ann, "name", "")
                        or _gv(ann, "title", "")
                    )
    return out


def _build_url_prompt(query: str, urls: list[str]) -> str:
    urls_text = "\n".join(f"- {u}" for u in urls)
    return (
        f"用户问题:\n{query}\n\n"
        f"请只基于以下 URL 的可访问内容回答, 并保留可靠 citation. "
        f"如果某个 URL 无法访问或没有可靠引用, 请明确说明未能验证.\n\n"
        f"URL 列表:\n{urls_text}\n\n请用中文回答."
    )


def _gv(obj: Any, name: str, default: Any = None) -> Any:
    """兼容 dict / pydantic / proto 对象的取值."""
    if isinstance(obj, dict):
        if name in obj:
            return obj[name]
        camel = name[0] + "".join(p.capitalize() for p in name.split("_")[1:])
        return obj.get(camel, default)
    if hasattr(obj, name):
        return getattr(obj, name)
    camel = name[0] + "".join(p.capitalize() for p in name.split("_")[1:])
    if hasattr(obj, camel):
        return getattr(obj, camel)
    return default


def _count_url_citations(block: Any) -> int:
    count = 0
    for ann in _gv(block, "annotations", []) or []:
        if _gv(ann, "type", "") == "url_citation" and _gv(ann, "url", ""):
            count += 1
    return count


def _extract_retrievals(step: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for item in _gv(step, "result", []) or []:
        url = _gv(item, "url", "")
        status = _normalize_status(_gv(item, "status", ""))
        if url or status:
            items.append({"url": str(url), "status": status})
    return items


def _normalize_status(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value).lower() if value is not None else ""


# ── 调度与输出 ────────────────────────────────────────────────────────
def run_capability(
    client: genai.Client, model: str, cap: str
) -> list[dict[str, Any]]:
    """对单个模型 + 单个能力跑全部测试用例."""
    results: list[dict[str, Any]] = []
    if cap == "search":
        for tc in SEARCH_QUERIES:
            r = test_search(client, model, tc["query"])
            results.append({"id": tc["id"], "note": tc["note"], **r})
            time.sleep(0.5)
    elif cap == "maps":
        for tc in MAPS_QUERIES:
            r = test_maps(client, model, tc["query"], tc["lat"], tc["lng"])
            results.append({"id": tc["id"], "note": tc["note"], **r})
            time.sleep(0.5)
    elif cap == "search_interactions":
        for tc in SEARCH_QUERIES:
            r = test_search_interactions(client, model, tc["query"])
            results.append({"id": tc["id"], "note": tc["note"], **r})
            time.sleep(0.5)
    elif cap == "maps_interactions":
        for tc in MAPS_QUERIES:
            r = test_maps_interactions(
                client, model, tc["query"], tc["lat"], tc["lng"]
            )
            results.append({"id": tc["id"], "note": tc["note"], **r})
            time.sleep(0.5)
    elif cap == "url_interactions":
        for tc in URL_CONTEXT_QUERIES:
            r = test_url_interactions(client, model, tc["query"], tc["urls"])
            results.append({"id": tc["id"], "note": tc["note"], **r})
            time.sleep(0.5)
    elif cap == "url_gencontent":
        for tc in URL_CONTEXT_QUERIES:
            r = test_url_gencontent(client, model, tc["query"], tc["urls"])
            results.append({"id": tc["id"], "note": tc["note"], **r})
            time.sleep(0.5)
    return results


def _verdict(results: list[dict[str, Any]]) -> str:
    """单能力汇总判定: REAL=元数据真实 / EMPTY=成功但无元数据 / FAIL=全失败."""
    if not results:
        return "-"
    if all(r.get("success") for r in results):
        if all(r.get("metadata_real") for r in results):
            return "REAL"
        if any(r.get("metadata_real") for r in results):
            return "PARTIAL"
        return "EMPTY"
    if any(r.get("success") for r in results):
        return "PARTIAL"
    return "FAIL"


def print_matrix(all_data: dict[str, dict[str, list[dict[str, Any]]]]) -> None:
    """打印模型 × 能力矩阵."""
    print(f"\n\n{'=' * 78}")
    print("能力矩阵 (REAL=接地真实生效 / EMPTY=成功但无元数据 / FAIL=失败)")
    print(f"{'=' * 78}")
    models = list(all_data.keys())
    header = f"{'model':<32s}" + "".join(
        f"{c:>18s}" for c in ALL_CAPABILITIES
    )
    print(header)
    print("-" * 78)
    for model in models:
        row = f"{model:<32s}"
        for cap in ALL_CAPABILITIES:
            verdict = _verdict(all_data[model].get(cap, []))
            row += f"{verdict:>18s}"
        print(row)
    print("-" * 78)


def print_detail(model: str, cap: str, results: list[dict[str, Any]]) -> None:
    print(f"\n{'─' * 70}")
    print(f"[{model}] {cap}")
    print(f"{'─' * 70}")
    for r in results:
        rid = r.get("id", "")
        note = r.get("note", "")
        if not r.get("success"):
            print(
                f"  {rid} FAIL [{r.get('error_type', '')}]: "
                f"{str(r.get('error', ''))[:160]}"
            )
            continue
        real = r.get("metadata_real")
        tag = "REAL" if real else "EMPTY"
        print(
            f"  {rid} {tag} | {r['elapsed']}s | "
            f"chunks={r.get('grounding_chunks') or r.get('maps_chunks') or 0} "
            f"queries={len(r.get('search_queries', []))} "
            f"cit={r.get('citations', 0)} "
            f"retrievals={len(r.get('retrievals', []) or r.get('url_metadata', []))}"
        )
        answer = r.get("answer", "")
        if answer:
            print(f"    {answer[:200]}{'...' if len(answer) > 200 else ''}")


def _json_default(obj: Any) -> Any:
    """JSON 兜底: bytes 转 <N bytes>, 其余转 str, 避免 --raw 序列化崩溃."""
    if isinstance(obj, (bytes, bytearray)):
        return f"<{len(obj)} bytes>"
    return str(obj)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemma Grounding 能力验证")
    parser.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_MODELS,
        help=f"待测模型 (默认: {' '.join(DEFAULT_MODELS)})",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="追加各能力的 Gemini 基线模型对照",
    )
    parser.add_argument(
        "--capabilities",
        nargs="+",
        default=ALL_CAPABILITIES,
        choices=ALL_CAPABILITIES,
        help="限定测试能力",
    )
    parser.add_argument("--save", action="store_true", help="保存结果 JSON")
    parser.add_argument(
        "--raw", action="store_true", help="结果含原始响应 (体积大)"
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client = create_client()

    models = list(args.models)
    if args.baseline:
        for cap in args.capabilities:
            for b in BASELINES.get(cap, []):
                if b not in models:
                    models.append(b)

    caps = args.capabilities
    print(f"模型: {models}")
    print(f"能力: {caps}")
    print(f"{'=' * 70}")

    # all_data[model][cap] = [results]
    all_data: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for model in models:
        all_data[model] = {}
        for cap in caps:
            results = run_capability(client, model, cap)
            all_data[model][cap] = results
            print_detail(model, cap, results)

    print_matrix(all_data)

    if args.save:
        ts = int(time.time())
        out: dict[str, Any] = {}
        for model, cap_map in all_data.items():
            out[model] = {}
            for cap, results in cap_map.items():
                out[model][cap] = [
                    r if args.raw else {k: v for k, v in r.items() if k != "raw"}
                    for r in results
                ]
        output_file = OUTPUT_DIR / f"results_{ts}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2, default=_json_default)
        print(f"\n结果已保存: {output_file}")


if __name__ == "__main__":
    main()
