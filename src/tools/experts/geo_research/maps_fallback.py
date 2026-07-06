"""Maps Grounding fallback - Gemini 不可用时的等效地理查询.

unified_geo_client.place_search(腾讯主+百度备) + 轻量 LLM 综合.
仅在 quick 模式 Gemini Maps Grounding 失败时调用;
deep 模式 Gemini 失败时直接进 Agent(自带 8 个地图工具), 不走本模块.
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.experts.geo_research import unified_geo_client
from src.tools.experts.llm_synthesis import synthesize_with_llm

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是专业的地理出行助手. 请基于提供的地图POI搜索结果回答问题, "
    "简洁控制在3-5段, 附上推荐地点的名称和相关信息. "
    "如果搜索结果与问题不完全匹配, 基于可用信息作答."
)


async def maps_fallback(
    query: str,
    *,
    language: str = "zh",
) -> dict[str, Any]:
    """Gemini Maps Grounding 不可用时的等效 fallback.

    Args:
        query: 地理/出行查询
        language: 回答语言

    Returns:
        成功: 标准结果 dict
        失败: 含 "error" 字段的 dict
    """
    try:
        search = await unified_geo_client.place_search(query)
    except Exception as e:
        logger.exception("maps_fallback 检索失败: %s", e)
        return _error(query, language, f"检索失败: {e}")

    if "error" in search:
        return _error(query, language, search["error"])

    places = search.get("places", [])
    if not places:
        return _error(query, language, "地图检索未返回结果")

    context = _format_context(places)
    try:
        answer = await synthesize_with_llm(
            _SYSTEM_PROMPT, query, context, language=language
        )
    except Exception as e:
        logger.exception("maps_fallback 综合失败: %s", e)
        return _error(query, language, f"综合失败: {e}")

    return {
        "result": answer + _format_sources(places),
        "query": query,
        "depth": "quick",
        "language": language,
        "tools_used": ["place_search", "llm_synthesis"],
        "elapsed_seconds": 0.0,
    }


def _format_context(places: list[dict[str, Any]]) -> str:
    lines = []
    for i, p in enumerate(places, 1):
        name = p.get("name", "")
        address = p.get("address", "")
        category = p.get("category", "")
        lat = p.get("lat")
        lng = p.get("lng")
        coord = f"\n坐标: {lat},{lng}" if lat and lng else ""
        lines.append(f"[{i}] {name}\n地址: {address}\n分类: {category}{coord}")
    return "\n\n".join(lines)


def _format_sources(places: list[dict[str, Any]]) -> str:
    names = [p.get("name") for p in places if p.get("name")]
    if not names:
        return ""
    sources_text = "\n".join(f"- {n}" for n in names)
    return f"\n\n**相关地点:**\n{sources_text}"


def _error(query: str, language: str, msg: str) -> dict[str, Any]:
    return {
        "result": f"地理查询暂时无法完成: {msg}",
        "query": query,
        "depth": "quick",
        "language": language,
        "tools_used": [],
        "elapsed_seconds": 0.0,
        "error": msg,
    }
