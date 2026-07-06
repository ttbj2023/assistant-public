"""Gemini Maps Grounding 核心模块.

通过LangChain ChatGoogleGenerativeAI + bind_tools(google_maps)实现,
复用项目统一的客户端缓存/密钥管理/配置体系.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.inference.llm.response_utils import content_to_text
from src.tools.experts.model_factory import ExpertModelFactory
from src.tools.shared.cache import ExpertCache, get_expert_cache

logger = logging.getLogger(__name__)

QUICK_SYSTEM_PROMPT = (
    "你是专业的地理出行助手. 请基于地图数据直接回答问题, "
    "简洁控制在3-5段, 附上推荐地点的名称和相关信息."
)

DEEP_GROUNDING_PROMPT = (
    "你是专业的地理出行助手. 请基于地图数据直接回答问题, "
    "提供全面详尽的回答, 包含具体地点名称,地址,评分等细节."
)


def _get_grounding_config() -> tuple[str, dict[str, Any], float, int]:
    """从配置中获取maps grounding参数: (model_id, model_params, timeout, max_retries)."""
    from src.config.inference_config import get_config as get_inference_config
    from src.config.retry_config import get_retry_config

    cfg = get_inference_config().experts
    retry_cfg = get_retry_config().grounding
    return (
        cfg.get_model_id("maps_grounding"),
        cfg.get_model_params("maps_grounding"),
        cfg.maps_grounding_timeout,
        retry_cfg.max_retries,
    )


def _parse_maps_response(response: Any) -> dict[str, Any]:
    """从LangChain响应中提取Maps Grounding元数据."""
    text = content_to_text(response.content)

    sources: list[dict[str, str]] = []
    search_queries: list[str] = []

    grounding_meta = response.response_metadata.get("grounding_metadata", {})
    if grounding_meta:
        for chunk in grounding_meta.get("grounding_chunks", []):
            maps_info = chunk.get("maps", {})
            if maps_info and (maps_info.get("uri") or maps_info.get("title")):
                sources.append({
                    "title": maps_info.get("title", ""),
                    "uri": maps_info.get("uri", ""),
                    "place_id": maps_info.get("placeId", ""),
                })
        search_queries = grounding_meta.get("web_search_queries", [])

    return {
        "answer": text,
        "sources": sources,
        "search_queries": search_queries,
        "maps_chunks_count": len(sources),
        "source": "maps_grounding",
    }


async def gemini_maps_grounding(
    query: str,
    *,
    lat: float | None = None,
    lng: float | None = None,
    system_instruction: str = "",
    language: str = "zh",
) -> dict[str, Any]:
    """Gemini Maps Grounding核心函数.

    通过LangChain ChatGoogleGenerativeAI + bind_tools(google_maps)调用.

    Args:
        query: 地理/出行查询
        lat: 用户纬度(可选, 用于位置个性化)
        lng: 用户经度(可选)
        system_instruction: 系统提示词
        language: 回答语言 "zh" / "en"

    Returns:
        成功: {"answer": str, "sources": list, "search_queries": list, ...}
        失败: {"answer": "", "error": str, ...}

    """
    cache = get_expert_cache()
    cache_key = ExpertCache.make_key(
        "maps_grounding",
        query=query,
        instruction=system_instruction,
        lang=language,
        lat=lat,
        lng=lng,
    )

    cached = await cache.get_geo(cache_key)
    if cached is not None:
        return json.loads(cached)

    try:
        result = await _execute_grounding(query, lat, lng, system_instruction, language)
    except Exception as e:
        logger.warning("Gemini Maps Grounding失败(降级): %s", e)
        result = {
            "answer": "",
            "error": str(e),
            "sources": [],
            "search_queries": [],
            "maps_chunks_count": 0,
            "source": "maps_grounding",
        }

    # 错误结果不缓存, 避免短暂故障被 TTL 锁定放大(由上层 fallback 兜底)
    if "error" not in result:
        await cache.set_geo(cache_key, json.dumps(result, ensure_ascii=False))
    return result


async def _execute_grounding(
    query: str,
    lat: float | None,
    lng: float | None,
    system_instruction: str,
    language: str,
) -> dict[str, Any]:
    """执行Gemini Maps Grounding调用."""
    from google.genai import types

    model_id, model_params, timeout, max_retries = _get_grounding_config()

    llm = ExpertModelFactory.create(model_id, **model_params)
    bound_llm = llm.bind(
        tools=[types.Tool(google_maps=types.GoogleMaps())],
        max_retries=max_retries,
        timeout=timeout,
    )

    lang_hint = "请用中文回答." if language == "zh" else "Please respond in English."
    messages: list[Any] = []
    if system_instruction:
        messages.append(SystemMessage(content=system_instruction))

    prompt = f"{query}\n\n{lang_hint}"
    if lat is not None and lng is not None:
        prompt += f"\n(用户位置: {lat},{lng})"

    messages.append(HumanMessage(content=prompt))

    response = await bound_llm.ainvoke(messages)
    return _parse_maps_response(response)
