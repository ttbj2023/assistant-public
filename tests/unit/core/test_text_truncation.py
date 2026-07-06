"""工具结果截断函数单元测试.

测试 src.core.text_truncation 模块的截断逻辑.
"""

from __future__ import annotations

from src.core.text_truncation import DEFAULT_MAX_CHARS, truncate_tool_result


class TestTruncateToolResult:
    """truncate_tool_result 测试."""

    def test_short_text_unchanged(self):
        """短文本原样返回."""
        text = "hello world"
        assert truncate_tool_result(text) == text

    def test_empty_string_unchanged(self):
        """空字符串原样返回."""
        assert truncate_tool_result("") == ""

    def test_none_returns_none(self):
        """None 输入返回 None."""
        assert truncate_tool_result(None) is None  # type: ignore[arg-type]

    def test_exact_limit_unchanged(self):
        """恰好等于上限的文本不截断."""
        text = "a" * DEFAULT_MAX_CHARS
        assert truncate_tool_result(text) == text

    def test_over_limit_truncated(self):
        """超长文本被截断, 包含截断标记."""
        text = "a" * 50000
        result = truncate_tool_result(text, max_chars=10000)

        assert len(result) < len(text)
        assert "已截断" in result
        assert "50000" in result

    def test_head_and_tail_preserved(self):
        """截断后保留头部和尾部内容."""
        head = "HEAD" * 100
        middle = "MIDDLE" * 10000
        tail = "TAIL" * 100
        text = head + middle + tail

        result = truncate_tool_result(text, max_chars=1000)

        assert result.startswith("HEAD")
        assert "TAIL" in result
        assert "已截断" in result

    def test_custom_max_chars(self):
        """自定义上限生效."""
        text = "x" * 5000
        result = truncate_tool_result(text, max_chars=1000)

        assert len(result) < 5000
        assert "已截断" in result
        assert "5000" in result
        assert "1000" in result

    def test_truncation_marker_format(self):
        """截断标记格式正确, 包含省略字符数和原始长度."""
        text = "a" * 10000
        result = truncate_tool_result(text, max_chars=1000)

        assert "原始长度 10000 字符" in result
        assert "上限 1000 字符" in result
