"""Web研究服务 - 双模式调度(quick/deep).

所有模式以Gemini Grounding为固定起手, 按深度分级:
- quick: Gemini直接返回 (3-6秒, 适合简单查询)
- deep: 语义缓存 → Gemini + Agent多渠道迭代研究 (60-200秒, 适合深度研究)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from langchain_core.tools import BaseTool

from src.tools.experts.web_research.grounding_search import (
    QUICK_SYSTEM_PROMPT,
    gemini_grounding_search,
)
from src.tools.experts.web_research.research_agent import get_research_agent
from src.tools.experts.web_research.search_fallback import search_fallback
from src.tools.experts.web_research.url_context import (
    extract_supported_urls,
    gemini_url_context,
)
from src.tools.experts.web_research.url_context_fallback import url_context_fallback
from src.tools.external.academic_search_tool import AcademicSearchTool
from src.tools.external.doubao_web_search import DoubaoSearchTool
from src.tools.external.web_fetch import WebFetchTool
from src.tools.external.zhipu_web_reader import ZhipuReaderTool
from src.tools.shared.semantic_cache import get_semantic_cache

logger = logging.getLogger(__name__)

_MCP_SEARCH_TOOLS = {"baidu_search"}


def _get_url_context_config() -> tuple[bool, str, float, float, int]:
    """获取 URL Context 配置."""
    from src.config.inference_config import get_config as get_inference_config

    cfg = get_inference_config().experts
    return (
        cfg.url_context_enabled,
        cfg.get_model_id("url_context"),
        cfg.url_context_quick_timeout,
        cfg.url_context_deep_timeout,
        cfg.url_context_max_urls,
    )


def _fallback_enabled() -> bool:
    """是否启用 Gemini Grounding fallback(等效工具降级)."""
    from src.config.inference_config import get_config as get_inference_config

    return get_inference_config().experts.grounding_fallback_enabled


def _grounding_error(
    query: str, depth: str, language: str, start_time: float, error: str
) -> dict[str, Any]:
    """构造 Grounding 失败(含 fallback 也失败)的标准错误返回."""
    return {
        "result": f"Gemini Grounding失败: {error}",
        "query": query,
        "depth": depth,
        "language": language,
        "elapsed_seconds": round(time.time() - start_time, 2),
        "error": "grounding_failed",
    }


async def run_web_research(
    query: str,
    depth: str = "deep",
    language: str = "zh",
    *,
    model_id: str = "",
    timeout: float = 360.0,
    llm_request_timeout: float = 90.0,
    mcp_bridge: Any | None = None,
) -> dict[str, Any]:
    """执行网络研究查询.

    Args:
        query: 自然语言研究查询
        depth: 研究深度 "quick" / "deep"
        language: 回答语言 "zh" / "en"
        model_id: Agent/综合LLM使用的模型ID
        timeout: Agent执行超时
        llm_request_timeout: LLM单次请求超时
        mcp_bridge: McpBridge实例(用于获取MCP工具)

    Returns:
        包含result/query/depth/language/elapsed_seconds/tools_used的dict

    """
    start_time = time.time()
    url_context_enabled, url_model_id, quick_url_timeout, deep_url_timeout, max_urls = (
        _get_url_context_config()
    )
    explicit_urls = extract_supported_urls(query, max_urls=max_urls)

    if depth == "quick" and explicit_urls and url_context_enabled:
        quick_url_context = await gemini_url_context(
            query,
            explicit_urls,
            language=language,
            timeout=quick_url_timeout,
            model_id=url_model_id,
        )
        if quick_url_context.get("verified"):
            result = _format_url_context_result(quick_url_context, query, language)
            result["elapsed_seconds"] = round(time.time() - start_time, 2)
            return result
        # Gemini URL Context 失败 → 降级到 zhipu_reader
        if _fallback_enabled():
            logger.info(
                "quick URL Context 失败, 降级到 zhipu_reader: %s",
                quick_url_context.get("error"),
            )
            fb = await url_context_fallback(query, explicit_urls, language=language)
            if "error" not in fb:
                fb["elapsed_seconds"] = round(time.time() - start_time, 2)
                return fb
        result = _format_url_context_result(quick_url_context, query, language)
        result["elapsed_seconds"] = round(time.time() - start_time, 2)
        return result

    # deep模式: 语义缓存查找 (命中则跳过grounding和agent)
    if depth == "deep":
        cache = get_semantic_cache()
        cached = await cache.get(query)
        if cached is not None:
            try:
                result = json.loads(cached)
                result["elapsed_seconds"] = round(time.time() - start_time, 2)
                result["cache_hit"] = True
                logger.info("deep模式语义缓存命中: query=%s", query[:50])
                return result
            except (json.JSONDecodeError, KeyError):
                logger.warning("语义缓存数据损坏, 降级为正常执行")

    # grounding (quick模式的最终结果, deep模式的输入)
    system_instruction = QUICK_SYSTEM_PROMPT
    url_context: dict[str, Any] | None = None
    if depth == "deep" and explicit_urls and url_context_enabled:
        gather_results = await asyncio.gather(
            gemini_grounding_search(
                query,
                system_instruction=system_instruction,
                language=language,
            ),
            gemini_url_context(
                query,
                explicit_urls,
                language=language,
                timeout=deep_url_timeout,
                model_id=url_model_id,
            ),
            return_exceptions=True,
        )
        grounding = gather_results[0]
        url_context = gather_results[1]
        # 并发任务之一抛异常时不牵连另一个: grounding 异常转错误 dict (下游
        # "error" in grounding 触发 fallback), url_context 异常降级为 None
        if isinstance(grounding, Exception):
            grounding = _grounding_error(
                query,
                depth,
                language,
                start_time,
                str(grounding),
            )
        if isinstance(url_context, Exception):
            logger.warning("url_context 并发失败, 降级为 None: %s", url_context)
            url_context = None
    else:
        grounding = await gemini_grounding_search(
            query,
            system_instruction=system_instruction,
            language=language,
        )

    has_verified_url_context = bool(url_context and url_context.get("verified"))
    grounding_failed = "error" in grounding and not grounding.get("answer")

    if grounding_failed and not has_verified_url_context:
        # grounding 与 url_context 均无可用结果
        if depth == "quick":
            # quick: 静默降级到 doubao_search + LLM 综合
            if _fallback_enabled():
                logger.info(
                    "quick Grounding 失败, 降级到 doubao_search: %s",
                    grounding.get("error"),
                )
                fb = await search_fallback(query, language=language)
                if "error" not in fb:
                    fb["elapsed_seconds"] = round(time.time() - start_time, 2)
                    return fb
            return _grounding_error(
                query, depth, language, start_time, grounding.get("error", "未知错误")
            )
        # deep: Agent 自带 doubao_search/fetch_webpage/zhipu_reader,
        # 跳过 Gemini 预处理直接让 Agent 自主研究
        if _fallback_enabled():
            logger.info(
                "deep Grounding 失败, 跳过直接进 Agent: %s", grounding.get("error")
            )
            grounding = None
        else:
            return _grounding_error(
                query, depth, language, start_time, grounding.get("error", "未知错误")
            )
    elif grounding_failed:
        # grounding 失败但有 verified URL Context 兜底 → 置空 grounding 进 Agent
        logger.info(
            "Grounding 失败但有 URL Context 兜底, 置空 grounding: %s",
            grounding.get("error"),
        )
        grounding = None

    if depth == "quick":
        result = _format_grounding_result(grounding, query, language)
        result["elapsed_seconds"] = round(time.time() - start_time, 2)
        return result

    # deep模式: 执行agent研究
    result = await _run_deep_research(
        query,
        grounding,
        language,
        start_time=start_time,
        model_id=model_id,
        timeout=timeout,
        llm_request_timeout=llm_request_timeout,
        mcp_bridge=mcp_bridge,
        url_context=url_context if has_verified_url_context else None,
    )

    # 成功结果写入语义缓存 (不缓存错误/超时结果)
    if "error" not in result:
        try:
            cache = get_semantic_cache()
            cache_payload = {k: v for k, v in result.items() if k != "elapsed_seconds"}
            await cache.put(query, json.dumps(cache_payload, ensure_ascii=False))
            logger.info("deep模式结果已写入语义缓存: query=%s", query[:50])
        except Exception as e:
            logger.warning("语义缓存写入异常(不影响结果): %s", e)

    return result


def _format_grounding_result(
    grounding: dict[str, Any],
    query: str,
    language: str,
) -> dict[str, Any]:
    """格式化grounding结果为标准返回格式."""
    answer = grounding["answer"]
    sources = grounding.get("sources", [])
    if sources:
        sources_text = "\n".join(f"- 来源: {s['domain']}" for s in sources)
        answer += f"\n\n**参考来源:**\n{sources_text}"
    return {
        "result": answer,
        "query": query,
        "depth": "quick",
        "language": language,
        "elapsed_seconds": 0.0,
        "tools_used": ["grounding_search"],
    }


def _format_url_context_result(
    url_context: dict[str, Any],
    query: str,
    language: str,
) -> dict[str, Any]:
    """格式化 URL Context 结果为标准返回格式."""
    if not url_context.get("verified"):
        reason = url_context.get("error") or url_context.get("message") or "未知原因"
        return {
            "result": f"Gemini URL Context 未取得可靠 citation: {reason}",
            "query": query,
            "depth": "quick",
            "language": language,
            "elapsed_seconds": 0.0,
            "tools_used": ["url_context"],
            "error": "url_context_unverified",
        }

    answer = url_context.get("answer", "")
    sources = _dedupe_sources(url_context.get("sources", []))
    if sources:
        sources_text = "\n".join(f"- [{s['title']}]({s['url']})" for s in sources)
        answer += f"\n\n**参考来源:**\n{sources_text}"
    return {
        "result": answer,
        "query": query,
        "depth": "quick",
        "language": language,
        "elapsed_seconds": 0.0,
        "tools_used": ["url_context"],
    }


def _dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        url = source.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append({"title": source.get("title", "") or url, "url": url})
    return deduped


async def _run_deep_research(
    query: str,
    grounding: dict[str, Any],
    language: str,
    *,
    start_time: float,
    model_id: str,
    timeout: float,
    llm_request_timeout: float,
    mcp_bridge: Any | None,
    url_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deep模式: grounding + Agent迭代研究."""
    agent_tools: list[BaseTool] = [
        WebFetchTool(),
        DoubaoSearchTool(),
        ZhipuReaderTool(),
        AcademicSearchTool(),
    ]

    if mcp_bridge is not None:
        all_mcp_tools = await mcp_bridge.get_all_tools()
        mcp_tools = [t for t in all_mcp_tools if t.name in _MCP_SEARCH_TOOLS]
        agent_tools.extend(mcp_tools)

    logger.info(f"deep模式工具集: {[t.name for t in agent_tools]}")

    agent = get_research_agent(
        model_id=model_id,
        tools=agent_tools,
        timeout=timeout,
        llm_request_timeout=llm_request_timeout,
    )
    result = await agent.research(
        query=query,
        language=language,
        grounding_context=grounding,
        url_context_context=url_context,
    )
    if url_context:
        tools_used = set(result.get("tools_used", []))
        tools_used.add("url_context")
        result["tools_used"] = sorted(tools_used)
    result["elapsed_seconds"] = round(time.time() - start_time, 2)
    return result
