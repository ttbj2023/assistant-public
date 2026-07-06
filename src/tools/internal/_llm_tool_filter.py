"""LLM 工具噪音过滤 - 使用本地 Qwen3-4B 对关键词匹配结果去噪.

当关键词匹配返回 >= min_tools_for_filter 个工具时, 调用本地 LLM 判断哪些工具真正相关.
如果 LLM 调用失败, 优雅降级返回全部关键词匹配结果.

模型与参数通过 inference.tool_filter 配置 (config.yaml), 与项目其他 LLM 调用范式一致.

设计原则:
- 关键词匹配已保证召回, 筛选模型只需去除噪音
- 不确定则保留: 宁可多返回工具, 也不漏掉
- LLM 失败 ≠ 功能失败, 降级后等同无 LLM 状态
- Prompt 针对 4B 小模型优化: 短指令 + 编号列表 + JSON 输出
- 使用工具 description 前 3 行作为筛选模型输入 (自然描述核心能力)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Prompt 设计: 去噪定位 — 排除肯定无关的工具, 不确定则保留
_SYSTEM_PROMPT = (
    "你是工具筛选器.根据用户查询, 排除候选工具中肯定不会用到的工具.\n"
    "不确定是否相关的, 一律保留.\n"
    '只返回JSON: {"relevant": [编号列表]}\n'
    "如果全部可能相关, 返回所有编号.\n"
    "不要解释, 不要输出其他内容."
)


def _extract_filter_desc(tool: dict) -> str:
    """提取工具 description 前 3 行作为筛选模型输入.

    前 3 行是工具的自然能力描述, 供筛选模型做语义判断.
    如果 full_description 为空, 回退到 description.
    """
    full = tool.get("full_description", "") or tool.get("description", "")
    lines = [line.strip() for line in full.split("\n") if line.strip()]
    return "\n".join(lines[:3]) if lines else tool.get("description", "")


def _build_user_message(query: str, candidates: list[dict[str, str]]) -> str:
    """构建用户消息: 编号列表 + description 前 3 行."""
    lines = [f"用户查询: {query}", "", "候选工具:"]
    for i, tool in enumerate(candidates, 1):
        name = tool.get("name", "")
        desc = _extract_filter_desc(tool)
        lines.append(f"{i}. {name}: {desc}")
    return "\n".join(lines)


def _parse_llm_response(content: Any) -> list[int] | None:
    """解析 LLM 返回的 JSON, 提取 relevant 编号列表."""
    text = content if isinstance(content, str) else str(content)
    text = text.strip()

    # 直接 JSON 解析
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "relevant" in data:
            indices = data["relevant"]
            if isinstance(indices, list):
                return [int(i) for i in indices if isinstance(i, (int, float))]
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # 正则提取 JSON 对象
    match = re.search(r"\{[^{}]*\}", text)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, dict) and "relevant" in data:
                indices = data["relevant"]
                if isinstance(indices, list):
                    return [int(i) for i in indices if isinstance(i, (int, float))]
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    logger.warning("无法解析 LLM 过滤响应: %s", text[:200])
    return None


async def _call_llm_filter(
    query: str,
    candidates: list[dict[str, str]],
) -> list[int] | None:
    """调用本地 LLM 进行工具筛选.

    Args:
        query: 用户原始查询
        candidates: 候选工具列表

    Returns:
        相关工具的 1-based 编号列表, 或 None 表示解析失败

    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from src.config.inference_config import get_config as get_inference_config
    from src.inference.llm.model_loader import create_llm
    from src.inference.usage import usage_source

    cfg = get_inference_config().tool_filter
    llm = create_llm(cfg.model)
    if cfg.model_params:
        llm = llm.bind(**cfg.model_params)

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=_build_user_message(query, candidates)),
    ]

    with usage_source("tool_llm"):
        response = await asyncio.wait_for(
            llm.ainvoke(messages),
            timeout=cfg.timeout,
        )

    return _parse_llm_response(response.content)


async def filter_tools_by_llm(
    query: str,
    candidates: list[dict[str, str]],
) -> list[dict[str, str]]:
    """使用 LLM 过滤候选工具, 返回真正相关的工具列表.

    Args:
        query: 用户原始查询
        candidates: 关键词匹配的候选工具列表

    Returns:
        LLM 判定相关的工具列表; 如果 LLM 调用失败, 返回原始 candidates

    """
    from src.config.inference_config import get_config as get_inference_config

    cfg = get_inference_config().tool_filter
    if len(candidates) < cfg.min_tools_for_filter:
        return candidates

    try:
        relevant_indices = await _call_llm_filter(query, candidates)
        if relevant_indices is None:
            logger.info(
                "LLM 过滤: 响应不可解析, 降级返回全部 %d 个候选", len(candidates)
            )
            return candidates

        # 将 1-based 编号转换为过滤后列表
        filtered = []
        for idx in relevant_indices:
            if 1 <= idx <= len(candidates):
                filtered.append(candidates[idx - 1])

        if not filtered:
            logger.info("LLM 过滤: 返回空列表, 降级返回全部 %d 个候选", len(candidates))
            return candidates

        logger.info(
            "LLM 过滤: query='%s', %d/%d 个工具被保留: %s",
            query,
            len(filtered),
            len(candidates),
            [t["name"] for t in filtered],
        )
        return filtered

    except Exception:
        logger.warning(
            "LLM 工具过滤失败, 降级返回全部 %d 个候选",
            len(candidates),
            exc_info=True,
        )
        return candidates


__all__ = ["filter_tools_by_llm"]
