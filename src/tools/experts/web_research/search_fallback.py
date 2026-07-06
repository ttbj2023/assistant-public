"""Search Grounding fallback - Gemini 不可用时的等效搜索综合.

doubao_web_search 检索 + 轻量 LLM 综合, 复刻 google_search grounding 的
"检索+生成"闭环. 仅在 quick 模式 Gemini 失败时被调用;
deep 模式 Gemini 失败时直接进 Agent(自带 doubao_search 等工具), 不走本模块.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from src.tools.experts.llm_synthesis import synthesize_with_llm
from src.tools.external.doubao_web_search import doubao_web_search

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是专业的研究助手. 请基于提供的搜索结果直接回答问题, "
    "简洁控制在3-5段, 在回答末尾附上参考来源链接."
)


async def search_fallback(
    query: str,
    *,
    language: str = "zh",
) -> dict[str, Any]:
    """Gemini Search Grounding 不可用时的等效 fallback.

    Args:
        query: 搜索查询
        language: 回答语言

    Returns:
        成功: 标准结果 dict(result/query/depth/language/tools_used/elapsed_seconds)
        失败: 含 "error" 字段的 dict
    """
    try:
        search = await doubao_web_search(query, count=8, timeout=30.0)
    except Exception as e:
        logger.exception("search_fallback 检索失败: %s", e)
        return _error(query, language, f"检索失败: {e}")

    if "error" in search:
        return _error(query, language, search["error"])

    results = search.get("search_results", [])
    if not results:
        return _error(query, language, "检索未返回结果")

    context = _format_context(results)
    try:
        answer = await synthesize_with_llm(
            _SYSTEM_PROMPT, query, context, language=language
        )
    except Exception as e:
        logger.exception("search_fallback 综合失败: %s", e)
        return _error(query, language, f"综合失败: {e}")

    return {
        "result": answer + _format_sources(results),
        "query": query,
        "depth": "quick",
        "language": language,
        "tools_used": ["doubao_search", "llm_synthesis"],
        "elapsed_seconds": 0.0,
    }


def _format_context(results: list[dict[str, Any]]) -> str:
    lines = []
    for i, item in enumerate(results, 1):
        title = item.get("title", "")
        link = item.get("link", "")
        content = item.get("content", "")
        lines.append(f"[{i}] {title}\n链接: {link}\n摘要: {content}")
    return "\n\n".join(lines)


def _format_sources(results: list[dict[str, Any]]) -> str:
    seen: set[str] = set()
    domains: list[str] = []
    for item in results:
        domain = _domain(item.get("link", ""))
        if domain and domain not in seen:
            seen.add(domain)
            domains.append(domain)
    if not domains:
        return ""
    sources_text = "\n".join(f"- 来源: {d}" for d in domains)
    return f"\n\n**参考来源:**\n{sources_text}"


def _domain(url: str) -> str:
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc
        return netloc.replace("www.", "") if netloc else ""
    except Exception:
        return ""


def _error(query: str, language: str, msg: str) -> dict[str, Any]:
    return {
        "result": f"搜索暂时不可用: {msg}",
        "query": query,
        "depth": "quick",
        "language": language,
        "tools_used": [],
        "elapsed_seconds": 0.0,
        "error": msg,
    }
