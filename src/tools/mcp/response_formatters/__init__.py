"""MCP响应格式化器注册表 - 配置名到格式化器实例的映射.

使用方式:
    from src.tools.mcp.response_formatters import get_formatter

    formatter = get_formatter("formatter_name")
    if formatter:
        text = formatter.safe_format(raw_response)
"""

from __future__ import annotations

from .base import BaseMcpResponseFormatter
from .registry import get_formatter, list_formatters, register

__all__ = ["BaseMcpResponseFormatter", "get_formatter", "list_formatters", "register"]
