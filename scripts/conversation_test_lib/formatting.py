"""输出格式化工具函数."""

from __future__ import annotations

SEPARATOR = "=" * 60
THIN_SEP = "-" * 50


def _truncate(text: str, max_len: int = 200) -> str:
    """截断字符串, 超出部分追加省略号."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _cyan(text: str) -> str:
    return f"\033[36m{text}\033[0m"


def _green(text: str) -> str:
    return f"\033[32m{text}\033[0m"


def _yellow(text: str) -> str:
    return f"\033[33m{text}\033[0m"
