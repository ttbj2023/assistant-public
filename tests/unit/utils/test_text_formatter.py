"""文本格式化工具测试.

测试 src.utils.text_formatter 模块中的文本处理和Markdown格式化功能。
"""

from __future__ import annotations

import pytest

from src.utils.text_formatter import (
    build_sections,
    create_conversation_round,
    validate_format_template,
)


class TestTextFormatting:
    """文本格式化测试类."""

    @pytest.mark.parametrize(
        "sections,separator,expected_count,check_content",
        [
            (["段落1", "段落2", "段落3"], "\n", 3, ["段落1", "段落2", "段落3"]),
            (
                ["段落1", "", "段落2", "   ", "段落3"],
                "\n",
                3,
                ["段落1", "段落2", "段落3"],
            ),
            (["段落1", "段落2"], " | ", None, "段落1 | 段落2"),
            (["", "", ""], "\n", 0, ""),
        ],
    )
    def test_build_sections_should_handle_various_inputs(
        self, sections, separator, expected_count, check_content
    ):
        result = build_sections(sections, separator)

        if isinstance(check_content, str):
            assert result == check_content
        else:
            lines = result.split("\n")
            non_empty_lines = [line for line in lines if line.strip()]
            if expected_count is not None:
                assert len(non_empty_lines) == expected_count
            for content in check_content:
                assert content in result

    def test_create_conversation_round_should_work_when_normal(self):
        result = create_conversation_round(5, "对话内容")
        assert "[Round 5]" in result
        assert "对话内容" in result

    def test_create_conversation_round_empty_should_work_when_content(self):
        result = create_conversation_round(5, "")
        assert result == ""

    @pytest.mark.parametrize(
        "format_type,default,expected",
        [
            ("markdown", None, "markdown"),
            ("html", None, "markdown"),
            ("html", "json", "json"),
            ("", None, "markdown"),
        ],
    )
    def test_validate_format_template_should_handle_various_inputs(
        self, format_type, default, expected
    ):
        if default is None:
            result = validate_format_template(format_type)
        else:
            result = validate_format_template(format_type, default)
        assert result == expected


class TestEdgeCases:
    """边界情况测试类."""

    def test_build_sections_various_should_work_when_separators(self):
        sections = ["段落1", "段落2"]

        result = build_sections(sections, "|")
        assert result == "段落1|段落2"

        result = build_sections(sections, " -- ")
        assert result == "段落1 -- 段落2"
