"""URL Context fallback - Gemini URL Context 不可用时的等效页面阅读.

zhipu_web_reader(JS 渲染) 抓取 URL 正文 + 轻量 LLM 综合.
仅在 quick 模式 URL Context 失败时调用; deep 模式 Agent 自带 zhipu_reader 工具.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.tools.experts.llm_synthesis import synthesize_with_llm
from src.tools.external.zhipu_web_reader import zhipu_web_reader

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是专业的研究助手. 请只基于提供的网页正文回答问题, "
    "简洁控制在3-5段, 在回答末尾附上参考来源链接. "
    "如果某些页面内容缺失, 基于可用内容作答."
)

_MAX_CONTENT_PER_URL = 4000


async def url_context_fallback(
    query: str,
    urls: list[str],
    *,
    language: str = "zh",
) -> dict[str, Any]:
    """Gemini URL Context 不可用时的等效 fallback.

    Args:
        query: 用户查询
        urls: 待阅读的 URL 列表
        language: 回答语言

    Returns:
        成功: 标准结果 dict
        失败: 含 "error" 字段的 dict
    """
    if not urls:
        return _error(query, language, "无可阅读的 URL")

    # 并发抓取各 URL 正文, 单个失败不影响其余
    read_tasks = [zhipu_web_reader(u, timeout=30.0) for u in urls]
    read_results = await asyncio.gather(*read_tasks, return_exceptions=True)

    pages: list[dict[str, Any]] = []
    for url, res in zip(urls, read_results, strict=True):
        if isinstance(res, Exception):
            logger.warning("url_context_fallback 抓取异常 %s: %s", url, res)
            continue
        if "error" in res:
            logger.warning(
                "url_context_fallback 抓取失败 %s: %s", url, res.get("error")
            )
            continue
        content = (res.get("content") or "")[:_MAX_CONTENT_PER_URL]
        pages.append({"url": url, "title": res.get("title") or url, "content": content})

    if not pages:
        return _error(query, language, "所有 URL 抓取失败")

    context = _format_context(pages)
    try:
        answer = await synthesize_with_llm(
            _SYSTEM_PROMPT, query, context, language=language
        )
    except Exception as e:
        logger.exception("url_context_fallback 综合失败: %s", e)
        return _error(query, language, f"综合失败: {e}")

    sources_text = "\n".join(f"- [{p['title']}]({p['url']})" for p in pages)
    return {
        "result": f"{answer}\n\n**参考来源:**\n{sources_text}",
        "query": query,
        "depth": "quick",
        "language": language,
        "tools_used": ["zhipu_reader", "llm_synthesis"],
        "elapsed_seconds": 0.0,
    }


def _format_context(pages: list[dict[str, Any]]) -> str:
    parts = []
    for p in pages:
        parts.append(f"## {p['title']}\nURL: {p['url']}\n\n{p['content']}")
    return "\n\n---\n\n".join(parts)


def _error(query: str, language: str, msg: str) -> dict[str, Any]:
    return {
        "result": f"页面阅读暂时不可用: {msg}",
        "query": query,
        "depth": "quick",
        "language": language,
        "tools_used": [],
        "elapsed_seconds": 0.0,
        "error": msg,
    }
