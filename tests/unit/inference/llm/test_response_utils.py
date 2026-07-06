"""response_utils 单元测试.

测试 content_to_text 对各种 LLM content 格式(str / list / 兜底)的归一化行为.
"""

from __future__ import annotations

import pytest

from src.inference.llm.response_utils import (
    content_to_text,
    filter_think_tags_streaming,
    strip_think_tags,
)


class TestContentToText:
    """测试 content_to_text 统一归一化."""

    def test_string_content_returned_unchanged(self):
        """str content 应原样返回."""
        assert content_to_text("hello") == "hello"

    def test_empty_string_returns_empty(self):
        """空字符串应返回空字符串."""
        assert content_to_text("") == ""

    def test_list_with_text_block_joins_text(self):
        """list 中包含标准 text 块时, 拼接 text 字段."""
        content = [{"type": "text", "text": "hello ", "extras": {"sig": "x"}}]
        assert content_to_text(content) == "hello "

    def test_list_with_multiple_text_blocks_joins_all(self):
        """多个 text 块应拼接."""
        content = [
            {"type": "text", "text": "hello "},
            {"type": "text", "text": "world"},
        ]
        assert content_to_text(content) == "hello world"

    def test_list_with_thinking_block_skips_non_text(self):
        """thinking 等非 text 块应被跳过."""
        content = [
            {"type": "thinking", "thinking": "reasoning..."},
            {"type": "text", "text": "answer"},
        ]
        assert content_to_text(content) == "answer"

    def test_list_with_bare_string_included(self):
        """list 中裸字符串应被包含."""
        content = ["plain ", {"type": "text", "text": "text"}]
        assert content_to_text(content) == "plain text"

    def test_list_with_dict_text_key_no_type(self):
        """dict 无 type 但有 text 键时, 仍取 text."""
        content = [{"text": "fallback text"}]
        assert content_to_text(content) == "fallback text"

    def test_list_without_text_or_string_returns_empty(self):
        """list 中无可识别文本部分时返回空字符串."""
        content = [{"type": "image_url", "image_url": "http://x"}]
        assert content_to_text(content) == ""

    def test_empty_list_returns_empty(self):
        """空 list 应返回空字符串."""
        assert content_to_text([]) == ""

    def test_non_string_non_list_is_stringified(self):
        """非 str 非 list 应 str() 兜底."""
        assert content_to_text(123) == "123"

    def test_none_returns_empty_string(self):
        """None 应视为空文本返回."""
        assert content_to_text(None) == ""


class TestStripThinkTags:
    """测试 strip_think_tags 跨供应商 think 标签过滤."""

    @pytest.mark.parametrize(
        "content,expected",
        [
            # 完整标签对 - think (MiniMax 风格)
            ("a<think>x</think>b", "ab"),
            ("<think>\nline1\nline2\n</think>\nresult", "result"),
            # 完整标签对 - thinking (qwen reasoning 模式变体)
            ("a<thinking>x</thinking>b", "ab"),
            ("<thinking>reasoning</thinking>visible", "visible"),
            # 孤立闭合标签 - qwen3.7-plus 多模态泄露核心格式
            ("reasoning</think>visible", "visible"),
            (
                "用户让我描述图片...\n（reasoning）\n</think>\n\n最终回复",
                "最终回复",
            ),
            ("reasoning</thinking>visible", "visible"),
            # 无标签 / 空
            ("no tags here", "no tags here"),
            ("", ""),
            # 边界
            ("reasoning</think>", ""),
            ("</think>only after", "only after"),
            ("<think>x</think>", ""),
            ("a<think>x</think>b<think>y</think>c", "abc"),
            # 开闭标签名不一致: 不应作为完整对移除, 但孤立闭合兜底
            ("<think>x</thinking>y", "y"),
            # 输入控制 token 不处理
            ("<|think|>prompt<|/think|>", "<|think|>prompt<|/think|>"),
            # 标签带空白
            ("<think >x</think >y", "y"),
            ("<thinking\n>reasoning</thinking\n>out", "out"),
        ],
    )
    def test_strip_think_tags(self, content: str, expected: str):
        """应正确移除 think/thinking 标签及 reasoning 内容."""
        assert strip_think_tags(content) == expected


class TestFilterThinkTagsStreaming:
    """测试 filter_think_tags_streaming 流式 think 标签过滤."""

    @pytest.mark.parametrize(
        "content,in_think,buffer,expected",
        [
            # thinking 变体完整对
            ("<thinking\n>reasoning</thinking\n>visible", False, "", "visible"),
            ("<thinking\n>partial", False, "", (True, "partial")),
            ("end</thinking\n>visible", True, "partialend", "visible"),
            # 孤立闭合防御 (块外突然出现闭合)
            ("reasoning</think>visible", False, "", "visible"),
            ("reasoning</think>", False, "", ""),
            ("</think>visible", False, "", "visible"),
            # 原有 think 完整对 (回归)
            ("你好世界", False, "", "你好世界"),
            ("normal text", False, "", "normal text"),
            ("<think\n>reasoning</think\n>", False, "", ""),
            ("<think\n>reasoning</think\n>visible", False, "", "visible"),
            (
                "before<think\n>reason</think\n>after",
                False,
                "",
                "beforeafter",
            ),
            ("<think\n>partial", False, "", (True, "partial")),
            ("more text", True, "partial", (True, "partialmore text")),
            ("end</think\n>visible", True, "partialend", "visible"),
            (
                "<think\n>a</think\n>b<think\n>c</think\n>d",
                False,
                "",
                "bd",
            ),
            ("before<think\n>secret", False, "", "before"),
            ("</think\n>", True, "", (False, "")),
        ],
    )
    def test_filter_think_tags_streaming(
        self,
        content: str,
        in_think: bool,
        buffer: str,
        expected: str | tuple[bool, str],
    ):
        """应正确处理各种 think/thinking 标签场景及跨 chunk 状态."""
        result = filter_think_tags_streaming(content, in_think, buffer)
        if isinstance(expected, tuple):
            assert isinstance(result, tuple)
            assert result[0] == expected[0]
            assert result[1] == expected[1]
        else:
            assert result == expected
