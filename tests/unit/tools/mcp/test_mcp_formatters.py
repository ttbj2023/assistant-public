"""MCP响应格式化器单元测试."""

from __future__ import annotations

from src.tools.mcp.response_formatters import get_formatter
from src.tools.mcp.response_formatters.base import BaseMcpResponseFormatter


class TestFormatterRegistry:
    """格式化器注册表测试."""

    def test_get_unknown_formatter(self) -> None:
        """测试获取未注册的格式化器"""
        f = get_formatter("nonexistent")
        assert f is None


class TestBaseFormatter:
    """基类工具方法测试."""

    def test_extract_text(self) -> None:
        """测试文本提取"""
        raw = ([{"type": "text", "text": "hello world"}], None)
        assert BaseMcpResponseFormatter.extract_text(raw) == "hello world"

    def test_extract_text_empty(self) -> None:
        """测试空响应提取"""
        assert BaseMcpResponseFormatter.extract_text(None) == ""
        assert BaseMcpResponseFormatter.extract_text(([], None)) == ""

    def test_fallback_tuple(self) -> None:
        """测试tuple降级"""
        raw = ([{"type": "text", "text": "fallback content"}], None)
        assert BaseMcpResponseFormatter._fallback(raw) == "fallback content"
