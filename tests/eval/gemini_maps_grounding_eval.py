"""Gemini Maps Grounding 效果测试.

直接使用 google-genai SDK, 绕过项目初始化.
环境变量: GEMINI_API_KEY, GEMINI_BASE_URL (中转节点)

用法:
    python tests/eval/gemini_maps_grounding_eval.py
    python tests/eval/gemini_maps_grounding_eval.py --save
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("需要安装: pip install google-genai")
    sys.exit(1)

OUTPUT_DIR = Path(__file__).parent / "maps_grounding_results"

TEST_QUERIES: list[dict[str, Any]] = [
    {
        "id": "01_poi_nearby_bj",
        "query": "北京南站附近有什么好吃的餐厅推荐? 要评分高的",
        "scenario": "explore_nearby",
        "lat": 39.865,
        "lng": 116.378,
        "note": "核心场景: POI搜索+评分",
    },
    {
        "id": "02_route_plan",
        "query": "从北京国贸到三里屯怎么走最快?",
        "scenario": "plan_trip",
        "lat": None,
        "lng": None,
        "note": "路线规划, 北京城区短途",
    },
    {
        "id": "03_location_qa",
        "query": "杭州西湖在哪个区? 面积多大? 有什么著名的景点?",
        "scenario": "ask_location",
        "lat": None,
        "lng": None,
        "note": "基础地理知识问答",
    },
    {
        "id": "04_address_resolve",
        "query": "望京SOHO的详细地址是什么? 邮编多少?",
        "scenario": "resolve_address",
        "lat": None,
        "lng": None,
        "note": "地址解析",
    },
    {
        "id": "05_tourism_sh",
        "query": "上海外滩周边有什么好玩的景点? 步行能到的",
        "scenario": "explore_nearby",
        "lat": 31.240,
        "lng": 121.490,
        "note": "旅游推荐, 带坐标",
    },
    {
        "id": "06_transit_route",
        "query": "从上海虹桥机场到迪士尼怎么坐地铁? 需要多长时间?",
        "scenario": "plan_trip",
        "lat": None,
        "lng": None,
        "note": "公交路线规划",
    },
    {
        "id": "07_overseas_sf",
        "query": "San Francisco Fisherman's Wharf附近有什么好酒店推荐?",
        "scenario": "explore_nearby",
        "lat": 37.808,
        "lng": -122.409,
        "note": "海外POI, Google Maps强项",
    },
    {
        "id": "08_weather_query",
        "query": "北京今天天气怎么样?",
        "scenario": "ask_location",
        "lat": None,
        "lng": None,
        "note": "天气查询, Maps不支持, 预期降级",
    },
    {
        "id": "09_compare_food",
        "query": "成都春熙路附近有什么必吃美食?",
        "scenario": "explore_nearby",
        "lat": 30.657,
        "lng": 104.081,
        "note": "美食推荐, 二线城市",
    },
    {
        "id": "10_long_route",
        "query": "从北京到上海自驾怎么走? 大概多远? 过路费多少?",
        "scenario": "plan_trip",
        "lat": None,
        "lng": None,
        "note": "长途路线规划",
    },
]


def create_client() -> genai.Client:
    """创建 Gemini 客户端."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    base_url = os.environ.get("GEMINI_BASE_URL", "")

    if not api_key:
        print("❌ GEMINI_API_KEY 环境变量未设置")
        sys.exit(1)

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["http_options"] = types.HttpOptions(base_url=base_url)

    return genai.Client(**client_kwargs)


def run_test(
    client: genai.Client,
    test_case: dict[str, Any],
    model: str = "gemini-3.1-flash-lite-preview",
) -> dict[str, Any]:
    """运行单个测试用例."""
    query = test_case["query"]
    lat = test_case.get("lat")
    lng = test_case.get("lng")

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
            model=model,
            contents=prompt,
            config=config,
        )
        elapsed = time.time() - start

        text = response.text or ""

        sources: list[dict[str, str]] = []
        search_queries: list[str] = []
        widget_token = ""

        if response.candidates:
            gm = response.candidates[0].grounding_metadata
            if gm:
                if gm.grounding_chunks:
                    for chunk in gm.grounding_chunks:
                        if chunk.maps:
                            sources.append({
                                "title": chunk.maps.title or "",
                                "uri": chunk.maps.uri or "",
                                "place_id": getattr(
                                    chunk.maps,
                                    "place_id",
                                    getattr(chunk.maps, "placeId", ""),
                                ),
                            })
                if gm.web_search_queries:
                    search_queries = list(gm.web_search_queries)
                if (
                    hasattr(gm, "google_maps_widget_context_token")
                    and gm.google_maps_widget_context_token
                ):
                    widget_token = gm.google_maps_widget_context_token[:50]

        return {
            "success": True,
            "answer": text,
            "sources": sources,
            "maps_chunks_count": len(sources),
            "search_queries": search_queries,
            "widget_token": bool(widget_token),
            "elapsed_seconds": round(elapsed, 2),
            "model": model,
        }
    except Exception as e:
        elapsed = time.time() - start
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "elapsed_seconds": round(elapsed, 2),
            "model": model,
        }


def print_result(test_case: dict[str, Any], result: dict[str, Any]) -> None:
    """打印单个测试结果."""
    print(f"\n{'=' * 70}")
    print(f"[{test_case['id']}] {test_case['query']}")
    print(
        f"场景: {test_case['scenario']} | "
        f"坐标: {'%.3f,%.3f' % (test_case['lat'], test_case['lng']) if test_case.get('lat') else '无'} | "
        f"备注: {test_case['note']}"
    )
    print(f"{'─' * 70}")

    if not result["success"]:
        print(
            f"❌ 失败 [{result.get('error_type', 'Error')}]: {result.get('error', '')[:200]}"
        )
        print(f"耗时: {result['elapsed_seconds']}s")
        return

    print(
        f"✅ 成功 | 耗时: {result['elapsed_seconds']}s | "
        f"引用: {result['maps_chunks_count']} | "
        f"Widget: {'有' if result.get('widget_token') else '无'}"
    )
    if result.get("search_queries"):
        print(f"内部搜索词: {result['search_queries']}")

    print(f"{'─' * 50}")
    answer = result["answer"]
    print(answer[:600] if len(answer) > 600 else answer)
    if len(answer) > 600:
        print(f"... (共 {len(answer)} 字)")

    if result["sources"]:
        print(f"{'─' * 50}")
        print("Google Maps 引用:")
        for s in result["sources"][:8]:
            print(f"  📍 {s['title']}")
            if s["uri"]:
                print(f"     {s['uri'][:80]}")


def print_summary(all_results: list[dict[str, Any]]) -> None:
    """打印汇总表格."""
    print(f"\n\n{'=' * 70}")
    print("📊 汇总")
    print(f"{'=' * 70}")

    success = [r for r in all_results if r["result"]["success"]]
    failed = [r for r in all_results if not r["result"]["success"]]

    if success:
        avg_time = sum(r["result"]["elapsed_seconds"] for r in success) / len(success)
        total_sources = sum(r["result"]["maps_chunks_count"] for r in success)
    else:
        avg_time = 0
        total_sources = 0

    print(f"总计: {len(all_results)} | 成功: {len(success)} | 失败: {len(failed)}")
    print(f"平均耗时: {avg_time:.2f}s | 总引用数: {total_sources}")

    print(f"\n{'─' * 70}")
    print(f"{'ID':<25s} {'状态':>4s} {'耗时':>6s} {'引用':>4s} {'查询概要'}")
    print(f"{'─' * 70}")
    for r in all_results:
        res = r["result"]
        status = "✅" if res["success"] else "❌"
        time_str = f"{res['elapsed_seconds']:.1f}s" if res["success"] else "-"
        sources = str(res.get("maps_chunks_count", 0)) if res["success"] else "-"
        query_short = r["query"][:28]
        print(f"{r['id']:<25s} {status:>4s} {time_str:>6s} {sources:>4s} {query_short}")

    if failed:
        print("\n❌ 失败详情:")
        for r in failed:
            print(
                f"  [{r['id']}] {r['result'].get('error_type', 'Error')}: {r['result'].get('error', '')[:150]}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini Maps Grounding 效果测试")
    parser.add_argument(
        "--model",
        default="gemini-3.1-flash-lite-preview",
        help="模型名称 (默认: gemini-3.1-flash-lite-preview)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="限制测试数量 (0=全部)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="保存结果到JSON",
    )
    parser.add_argument(
        "--id",
        default="",
        dest="test_id",
        help="只运行指定ID的测试 (如 01_poi_nearby_bj)",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    client = create_client()

    queries = TEST_QUERIES
    if args.test_id:
        queries = [q for q in queries if q["id"] == args.test_id]
        if not queries:
            print(f"❌ 未找到测试: {args.test_id}")
            print(f"可用ID: {[q['id'] for q in TEST_QUERIES]}")
            sys.exit(1)
    elif args.limit > 0:
        queries = queries[: args.limit]

    print(f"模型: {args.model} | 测试数: {len(queries)}")
    print(f"{'=' * 70}")

    all_results: list[dict[str, Any]] = []
    for tc in queries:
        result = run_test(client, tc, model=args.model)
        print_result(tc, result)
        all_results.append({
            "id": tc["id"],
            "query": tc["query"],
            "scenario": tc["scenario"],
            "note": tc["note"],
            "result": result,
        })
        time.sleep(0.5)

    print_summary(all_results)

    if args.save:
        output_file = OUTPUT_DIR / f"results_{int(time.time())}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        print(f"\n📁 结果已保存: {output_file}")


if __name__ == "__main__":
    main()
