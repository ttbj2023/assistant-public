"""地理出行研究服务 - 双模式调度(quick/deep).

所有模式以Gemini Maps Grounding为固定起手, 按深度分级:
- quick: Gemini Maps Grounding直接返回
- deep: Gemini Grounding + 专家Agent + 百度API工具补充核实
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from src.config.retry_config import get_retry_config
from src.inference.llm.response_utils import content_to_text
from src.inference.llm.retry_predicates import (
    format_llm_failure_message,
    is_retryable_llm_exception,
)
from src.tools.experts.agent_utils import enable_tool_error_handling
from src.tools.experts.geo_research.unified_geo_tools import create_geo_sub_tools
from src.tools.experts.model_factory import ExpertModelFactory

from .maps_fallback import maps_fallback
from .maps_grounding import (
    DEEP_GROUNDING_PROMPT,
    QUICK_SYSTEM_PROMPT,
    gemini_maps_grounding,
)

logger = logging.getLogger(__name__)

_GEO_DEEP_SYSTEM_PROMPT = """你是地理出行专家.

## 核心原则
1. 工具调用次数 ≤ 3 次
2. 若提供了 Gemini Maps 初步结果, 优先采纳, 只在明确缺失时补充
3. 若未提供 Gemini 结果, 直接用地图工具(place_search/geocode/directions 等)完成查询
4. 不为 LLM 自身能答的内容调用工具 (地理知识/文化/历史 → 你直接答)

## 决策树

[路线规划]
- Gemini 已给距离/时间/路线 → 直接采纳
- Gemini 自承"无法提供" 或 数据明显异常
  → driving_directions / transit_directions / walking_directions

[行政区划]
- Gemini 列表完整 → 直接采纳
- Gemini 把景点/地标当区(如"拙政园是区") → 调用 district_search

[POI 查询]
- Gemini 已给地点+地址 → 直接采纳
- 需电话/评分等细节 → place_search

[坐标]
- 需要精确经纬度 → geocode
- 已有坐标需地址 → reverse_geocode

[实时路况]
- 用户明确问"现在是否堵车" → traffic
- 一般路线规划无需调用

[无需工具的情况]
- Gemini 已完整答出
- 地理知识/文化背景 (你直接答)
- 天气查询 (让用户重试, 主 Agent 有 weather_query)
"""


def _fallback_enabled() -> bool:
    """是否启用 Gemini Grounding fallback(等效工具降级)."""
    from src.config.inference_config import get_config as get_inference_config

    return get_inference_config().experts.grounding_fallback_enabled


def _grounding_error(
    query: str, depth: str, language: str, start_time: float, error: str
) -> dict[str, Any]:
    """构造 Grounding 失败(含 fallback 也失败)的标准错误返回."""
    return {
        "result": f"Gemini Maps Grounding失败: {error}",
        "query": query,
        "depth": depth,
        "language": language,
        "elapsed_seconds": round(time.time() - start_time, 2),
        "error": "grounding_failed",
    }


async def run_geo_research(
    query: str,
    depth: str = "quick",
    language: str = "zh",
    *,
    model_id: str = "",  # noqa: ARG001
    timeout: float = 120.0,
    mcp_bridge: Any | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    """执行地理出行研究查询.

    Args:
        query: 自然语言地理/出行查询
        depth: 研究深度 "quick" / "deep"
        language: 回答语言 "zh" / "en"
        model_id: Agent使用的模型ID
        timeout: Agent执行超时
        mcp_bridge: 不再使用, 保留接口兼容

    Returns:
        包含result/query/depth/language/elapsed_seconds/tools_used的dict

    """
    start_time = time.time()

    system_instruction = (
        DEEP_GROUNDING_PROMPT if depth == "deep" else QUICK_SYSTEM_PROMPT
    )
    grounding = await gemini_maps_grounding(
        query,
        system_instruction=system_instruction,
        language=language,
    )

    grounding_failed = "error" in grounding and not grounding.get("answer")
    if grounding_failed:
        if depth == "quick":
            # quick: 静默降级到 place_search + LLM 综合
            if _fallback_enabled():
                logger.info(
                    "quick Maps Grounding 失败, 降级到 place_search: %s",
                    grounding.get("error"),
                )
                fb = await maps_fallback(query, language=language)
                if "error" not in fb:
                    fb["elapsed_seconds"] = round(time.time() - start_time, 2)
                    return fb
            return _grounding_error(
                query, depth, language, start_time, grounding.get("error", "未知错误")
            )
        # deep: Agent 自带 8 个地图工具, 跳过 Gemini 预处理直接让 Agent 自主研究
        if _fallback_enabled():
            logger.info(
                "deep Maps Grounding 失败, 跳过直接进 Agent: %s",
                grounding.get("error"),
            )
            grounding = None
        else:
            return _grounding_error(
                query, depth, language, start_time, grounding.get("error", "未知错误")
            )

    if depth == "quick":
        result = _format_grounding_result(grounding, query, language)
        result["elapsed_seconds"] = round(time.time() - start_time, 2)
        return result

    return await _run_deep(
        query,
        grounding,
        language,
        start_time=start_time,
        timeout=timeout,
    )


def _format_grounding_result(
    grounding: dict[str, Any],
    query: str,
    language: str,
) -> dict[str, Any]:
    """格式化grounding结果为标准返回格式."""
    answer = grounding["answer"]
    sources = grounding.get("sources", [])
    if sources:
        sources_text = "\n".join(
            f"- [{s['title']}]({s['uri']})" for s in sources if s.get("title")
        )
        if sources_text:
            answer += f"\n\n**参考来源:**\n{sources_text}"
    return {
        "result": answer,
        "query": query,
        "depth": "quick",
        "language": language,
        "elapsed_seconds": 0.0,
        "tools_used": ["maps_grounding"],
    }


async def _run_deep(
    query: str,
    grounding: dict[str, Any],
    language: str,
    *,
    start_time: float,
    timeout: float,
) -> dict[str, Any]:
    """Deep模式: grounding结果作为上下文 + 专家Agent + 统一地图工具."""
    geo_tools = create_geo_sub_tools()

    llm = ExpertModelFactory.create_for_tool("geo_navigator")

    retry_cfg = get_retry_config().expert_agent
    agent = create_agent(
        llm,
        geo_tools,
        system_prompt=_GEO_DEEP_SYSTEM_PROMPT,
        middleware=[
            ModelRetryMiddleware(
                max_retries=retry_cfg.max_retries,
                retry_on=is_retryable_llm_exception,
                on_failure=format_llm_failure_message,
                initial_delay=retry_cfg.initial_delay,
                max_delay=retry_cfg.max_delay,
            ),
        ],
    )
    enable_tool_error_handling(agent)

    lang_hint = "请用中文回答." if language == "zh" else "Please respond in English."

    if grounding:
        grounding_summary = grounding.get("answer", "")
        grounding_sources = grounding.get("sources", [])
        sources_text = ""
        if grounding_sources:
            sources_text = "\n".join(
                f"- {s.get('title', '未知')}" for s in grounding_sources[:10]
            )
        prompt = (
            f"## 用户问题\n{query}\n\n## Gemini Maps 初步结果\n{grounding_summary}\n\n"
        )
        if sources_text:
            prompt += f"## Gemini引用的地点\n{sources_text}\n\n"
        prompt += (
            "## 要求\n基于以上信息, 结合地图工具补充核实, 给出最终综合回答. "
            f"{lang_hint}"
        )
    else:
        # 无 Gemini 预处理(Grounding 不可用), 直接让 Agent 用工具研究
        prompt = (
            f"## 用户问题\n{query}\n\n## 要求\n本次未获得 Gemini Maps 预处理结果, "
            "请直接使用地图工具(place_search/geocode/driving_directions/"
            f"transit_directions 等)完成查询, 给出综合回答. {lang_hint}"
        )

    try:
        result = await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [HumanMessage(content=prompt)]},
                config=RunnableConfig(max_concurrency=1, recursion_limit=16),
            ),
            timeout=timeout,
        )

        final_message = result["messages"][-1]
        raw_answer = content_to_text(
            final_message.content
            if hasattr(final_message, "content")
            else str(final_message)
        )

        elapsed = time.time() - start_time
        return {
            "result": raw_answer,
            "query": query,
            "depth": "deep",
            "language": language,
            "elapsed_seconds": round(elapsed, 2),
            "tools_used": ["maps_grounding", "geo_sub_tools"],
        }

    except TimeoutError:
        elapsed = time.time() - start_time
        logger.warning(f"Geo deep模式执行超时({timeout}s)")
        return {
            "result": f"地理查询超时({timeout}秒), 请尝试简化查询或使用quick模式.",
            "query": query,
            "depth": "deep",
            "language": language,
            "elapsed_seconds": round(elapsed, 2),
            "error": "timeout",
        }
    except Exception as e:
        elapsed = time.time() - start_time
        logger.exception("Geo deep模式执行失败: %s", e)
        return {
            "result": f"地理查询出错: {e!s}",
            "query": query,
            "depth": "deep",
            "language": language,
            "elapsed_seconds": round(elapsed, 2),
            "error": str(e),
        }
