"""工具结果截断工具 - 防止超长内容撑爆模型上下文.

提供统一的文本截断函数, 作为所有工具返回结果的安全网.
截断策略: 保留头部(70%) + 尾部(20%), 中间插入截断提示.
"""

from __future__ import annotations

DEFAULT_MAX_CHARS = 30000
_HEAD_RATIO = 0.7
_TAIL_RATIO = 0.2


def truncate_tool_result(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """截断工具返回结果, 防止超长内容撑爆模型上下文.

    超长时保留头部70% + 尾部20%, 中间插入截断提示.
    未超长时原样返回.

    Args:
        text: 工具返回的文本
        max_chars: 最大字符数, 默认30000

    Returns:
        截断后的文本(未超长时原样返回)

    """
    if not text or len(text) <= max_chars:
        return text

    head_chars = int(max_chars * _HEAD_RATIO)
    tail_chars = int(max_chars * _TAIL_RATIO)
    omitted = len(text) - head_chars - tail_chars
    marker = (
        f"\n\n... [已截断: 省略中间 {omitted} 字符, "
        f"原始长度 {len(text)} 字符, 上限 {max_chars} 字符] ...\n\n"
    )
    return text[:head_chars] + marker + text[-tail_chars:]


__all__ = ["DEFAULT_MAX_CHARS", "truncate_tool_result"]
