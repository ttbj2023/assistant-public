"""Gemini Search Grounding 核心模块.

通过LangChain ChatGoogleGenerativeAI + bind_tools(google_search)实现,
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
    "你是专业的研究助手. 请基于搜索结果直接回答问题, "
    "简洁控制在3-5段, 在回答末尾附上参考来源链接."
)


def _get_grounding_config() -> tuple[str, dict[str, Any], float, int]:
    """从配置中获取grounding参数: (model_id, model_params, timeout, max_retries)."""
    from src.config.inference_config import get_config as get_inference_config
    from src.config.retry_config import get_retry_config

    cfg = get_inference_config().experts
    retry_cfg = get_retry_config().grounding
    return (
        cfg.get_model_id("grounding"),
        cfg.get_model_params("grounding"),
        cfg.grounding_timeout,
        retry_cfg.max_retries,
    )


def _parse_grounding_response(response: Any) -> dict[str, Any]:
    """从LangChain响应中提取grounding元数据."""
    text = content_to_text(response.content)

    sources: list[dict[str, str]] = []
    search_queries: list[str] = []

    grounding_meta = response.response_metadata.get("grounding_metadata", {})
    if grounding_meta:
        # Google 已将 grounding_chunks[].web.uri 改为 vertexaisearch 死链 redirect,
        # 仅 title 字段携带真实源域名, 故只取域名做来源归因.
        for chunk in grounding_meta.get("grounding_chunks", []):
            web = chunk.get("web", {})
            domain = web.get("title", "")
            if domain:
                sources.append({"domain": domain})
        search_queries = grounding_meta.get("web_search_queries", [])

    return {
        "answer": text,
        "sources": sources,
        "search_queries": search_queries,
        "source": "grounding_search",
    }


async def gemini_grounding_search(
    query: str,
    *,
    system_instruction: str = "",
    language: str = "zh",
) -> dict[str, Any]:
    """Gemini Grounding核心函数.

    通过LangChain ChatGoogleGenerativeAI + bind_tools(google_search)调用,
    复用项目统一客户端缓存/密钥管理/配置体系.

    Args:
        query: 搜索查询(支持长文本上下文)
        system_instruction: 系统提示词, 控制回答风格
        language: 回答语言 "zh" / "en"

    Returns:
        成功: {"answer": str, "sources": list, "search_queries": list, "source": "grounding_search"}
        失败: {"answer": "", "error": str, "sources": [], "search_queries": [], "source": "grounding_search"}

    """
    cache = get_expert_cache()
    cache_key = ExpertCache.make_key(
        "grounding",
        query=query,
        instruction=system_instruction,
        lang=language,
    )

    cached = await cache.get_search(cache_key)
    if cached is not None:
        return json.loads(cached)

    try:
        result = await _execute_grounding(query, system_instruction, language)
    except Exception as e:
        logger.warning("Gemini Grounding失败(降级到Agent): %s", e)
        result = {
            "answer": "",
            "error": str(e),
            "sources": [],
            "search_queries": [],
            "source": "grounding_search",
        }

    # 错误结果不缓存, 避免短暂故障被 TTL 锁定放大(由上层 fallback 兜底)
    if "error" not in result:
        await cache.set_search(cache_key, json.dumps(result, ensure_ascii=False))
    return result


async def _execute_grounding(
    query: str,
    system_instruction: str,
    language: str,
) -> dict[str, Any]:
    """执行Gemini Grounding调用."""
    from google.genai import types

    model_id, model_params, timeout, max_retries = _get_grounding_config()

    llm = ExpertModelFactory.create(model_id, **model_params)
    bound_llm = llm.bind(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        max_retries=max_retries,
        timeout=timeout,
    )

    lang_hint = "请用中文回答." if language == "zh" else "Please respond in English."
    messages: list[Any] = []
    if system_instruction:
        messages.append(SystemMessage(content=system_instruction))
    messages.append(HumanMessage(content=f"{query}\n\n{lang_hint}"))

    response = await bound_llm.ainvoke(messages)
    return _parse_grounding_response(response)
