"""文本格式化工具 - 统一字符串处理和Markdown格式化逻辑.

提供安全的文本处理,内容清理,Markdown构建等通用格式化功能.
主要用于统一处理文本内容的安全格式化和展示.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_sections(sections: list[str], separator: str = "\n\n") -> str:
    """统一的段落构建逻辑.

    将多个段落用指定的分隔符连接,过滤空段落.

    Args:
        sections: 段落列表
        separator: 分隔符,默认为两个换行符

    Returns:
        连接后的段落文本

    """
    valid_sections = [section for section in sections if section and section.strip()]
    if not valid_sections:
        return ""

    return separator.join(valid_sections)


def create_conversation_round(round_number: int, content: str) -> str:
    """创建对话轮次格式化内容.

    Args:
        round_number: 对话轮次号
        content: 对话内容

    Returns:
        格式化的对话轮次

    """
    if not content or not content.strip():
        return ""

    header = f"[Round {round_number}]"
    return f"{header}\n{content.strip()}"


def validate_format_template(format_template: str, default: str = "markdown") -> str:
    """验证格式模板类型.

    Args:
        format_template: 要验证的格式模板
        default: 默认格式模板

    Returns:
        验证后的格式模板名称

    """
    if format_template != "markdown":
        logger.warning("不支持的格式模板: %s,使用默认%s", format_template, default)
        return default

    return format_template


__all__ = [
    "build_sections",
    "create_conversation_round",
    "validate_format_template",
]
