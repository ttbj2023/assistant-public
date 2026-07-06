"""对话格式化器测试.

测试新的存储层格式化功能，确保格式化逻辑正确下沉到存储层。
"""

from unittest.mock import patch

import pytest

from src.storage.formatters.conversation_formatter import (
    ConversationFormatter,
    create_conversation_formatter,
)
from src.utils import format_date_short, format_timestamp


class TestConversationFormatter:
    """对话格式化器测试类."""

    @pytest.mark.asyncio
    async def test_format_conversation_range_empty_data(self) -> None:
        """测试格式化空对话范围."""
        formatter = create_conversation_formatter()
        result = await formatter.format_conversation_range([])
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_conversation_range_single_round(self) -> None:
        """测试格式化单个对话轮次."""
        formatter = create_conversation_formatter()
        conversation_rounds = [
            {
                "round_number": 1,
                "user_message": "你好",
                "assistant_response": "你好！有什么可以帮助您的吗？",
                "created_at": "2023-01-01T10:00:00",
            }
        ]

        result = await formatter.format_conversation_range(conversation_rounds)

        assert "[Round 1]" in result
        assert "你好" in result
        assert "你好！有什么可以帮助您的吗？" in result

    @pytest.mark.asyncio
    async def test_format_conversation_range_multiple_rounds(self) -> None:
        """测试格式化多个对话轮次."""
        formatter = create_conversation_formatter()
        conversation_rounds = [
            {
                "round_number": 1,
                "user_message": "你好",
                "assistant_response": "你好！",
                "created_at": "2023-01-01T10:00:00",
            },
            {
                "round_number": 2,
                "user_message": "今天天气怎么样？",
                "assistant_response": "今天天气很好，阳光明媚。",
                "created_at": "2023-01-01T10:05:00",
            },
        ]

        result = await formatter.format_conversation_range(conversation_rounds)

        assert "[Round 1]" in result
        assert "[Round 2]" in result
        assert "你好" in result
        assert "今天天气怎么样？" in result
        assert "今天天气很好，阳光明媚。" in result
        assert "---" in result  # 轮次分隔符

    @pytest.mark.asyncio
    async def test_format_single_round(self) -> None:
        """测试格式化单个对话轮次."""
        formatter = create_conversation_formatter()
        round_data = {
            "round_number": 1,
            "user_message": "测试用户消息",
            "assistant_response": "测试助手回复",
            "created_at": "2023-01-01T10:00:00",
        }

        result = await formatter.format_single_round(round_data)

        assert "User: 测试用户消息" in result
        assert "Assistant: 测试助手回复" in result

    @pytest.mark.asyncio
    async def test_format_index_range_empty_data(self) -> None:
        """测试格式化空索引范围."""
        formatter = create_conversation_formatter()
        result = await formatter.format_index_range([])
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_index_range_single_item(self) -> None:
        """测试格式化单个索引项."""
        formatter = create_conversation_formatter()
        index_data = [
            {
                "round_number": 1,
                "summary": "关于天气的对话",
                "topic": "天气查询",
                "created_at": "2023-01-01T10:00:00",
            }
        ]

        result = await formatter.format_index_range(index_data)

        assert "<index>" in result
        assert "Round | Topic | Summary | Time" in result
        assert "1 | 天气查询 | 关于天气的对话 | 2023-01-01" in result

    @pytest.mark.asyncio
    async def test_format_index_range_multiple_items(self) -> None:
        """测试格式化多个索引项."""
        formatter = create_conversation_formatter()
        index_data = [
            {
                "round_number": 1,
                "summary": "简短的问候",
                "topic": "打招呼",
                "created_at": "2023-01-01T10:00:00",
            },
            {
                "round_number": 2,
                "summary": "这是一个非常非常长的摘要内容，用来测试截断功能是否能够正常工作。当摘要内容的长度超过了八十个字符的限制时，系统应该会自动截断并在末尾加上省略号，以避免表格显示时出现过长的列内容而影响整体的显示效果和用户体验。",
                "topic": "长内容测试",
                "created_at": "2023-01-01T10:05:00",
            },
        ]

        result = await formatter.format_index_range(index_data)

        assert "<index>" in result
        assert "1 | 打招呼 | 简短的问候" in result
        assert "2 | 长内容测试" in result
        assert "..." in result  # 检查截断标志

    @pytest.mark.asyncio
    async def test_format_conversation_range_invalid_round_skipped(self) -> None:
        """测试格式化时跳过无效对话轮次数据."""
        formatter = create_conversation_formatter()
        conversation_rounds = [
            None,
            "not_a_dict",
            {"round_number": 1, "user_message": "有效", "assistant_response": "有效回复"},
        ]

        result = await formatter.format_conversation_range(conversation_rounds)

        assert "[Round 1]" in result
        assert "有效" in result
        assert "有效回复" in result

    @pytest.mark.asyncio
    async def test_format_conversation_range_exception_raises_value_error(self) -> None:
        """测试格式化对话范围：异常时应抛出ValueError."""
        formatter = create_conversation_formatter()
        with patch(
            "src.storage.formatters.conversation_formatter.validate_format_template",
            side_effect=ValueError("模拟模板验证失败"),
        ):
            with pytest.raises(ValueError, match="对话范围格式化失败"):
                await formatter.format_conversation_range(
                    [{"round_number": 1}],
                )

    @pytest.mark.asyncio
    async def test_format_single_round_empty_messages(self) -> None:
        """测试格式化单个对话轮次：用户和助手消息均为空时应返回空串."""
        formatter = create_conversation_formatter()
        result = await formatter.format_single_round(
            {"round_number": 2, "user_message": "", "assistant_response": ""},
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_single_round_exception_returns_empty_string(self) -> None:
        """测试格式化单个对话轮次：异常时返回空字符串, 不向LLM注入错误文案."""
        formatter = create_conversation_formatter()
        with patch(
            "src.storage.formatters.conversation_formatter.validate_format_template",
            side_effect=ValueError("模拟错误"),
        ):
            result = await formatter.format_single_round({"round_number": 5})
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_index_range_non_markdown_template(self) -> None:
        """测试格式化索引范围：非markdown模板时降级为markdown."""
        formatter = create_conversation_formatter()
        index_data = [
            {
                "round_number": 1,
                "summary": "测试摘要",
                "topic": "测试",
                "created_at": "2023-01-01T10:00:00",
            },
        ]
        result = await formatter.format_index_range(index_data, format_template="html")
        assert "<index>" in result
        assert "1 | 测试 | 测试摘要" in result

    @pytest.mark.asyncio
    async def test_format_index_range_exception_returns_empty_string(self) -> None:
        """测试格式化索引范围：异常时返回空字符串, 不向LLM注入错误文案."""
        formatter = create_conversation_formatter()
        with patch(
            "src.storage.formatters.conversation_formatter.format_date_short",
            side_effect=ValueError("格式化日期失败"),
        ):
            result = await formatter.format_index_range(
                [{
                    "round_number": 1,
                    "summary": "test",
                    "created_at": "2023-01-01T10:00:00",
                }],
            )
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_index_groups_basic(self) -> None:
        """测试格式化索引分组的基本功能."""
        formatter = create_conversation_formatter()
        groups_data = [
            {"round_start": 1, "round_end": 3, "arc_phrase": "初次问候与天气讨论"},
            {"round_start": 4, "round_end": 4, "arc_phrase": "技术问题咨询"},
        ]

        result = await formatter.format_index_groups(groups_data)

        assert "<timeline>" in result
        assert "| 1-3 | 初次问候与天气讨论 |" in result
        assert "| 4 | 技术问题咨询 |" in result

    @pytest.mark.asyncio
    async def test_format_index_groups_empty(self) -> None:
        """测试格式化空索引分组."""
        formatter = create_conversation_formatter()
        result = await formatter.format_index_groups([])
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_index_groups_exception_returns_empty_string(self) -> None:
        """测试格式化索引分组：异常时返回空字符串."""
        formatter = create_conversation_formatter()
        result = await formatter.format_index_groups(["not_a_dict"])
        assert result == ""

    def test_format_timestamp(self) -> None:
        """测试时间戳格式化 - 使用统一工具函数."""
        # 字符串时间戳
        result1 = format_timestamp("2023-01-01T10:00:00.123456")
        assert result1 == "2023-01-01 10:00"

        # 空值
        result2 = format_timestamp("")
        assert result2 == ""

        result3 = format_timestamp(None)
        assert result3 == ""

    def test_format_date_short(self) -> None:
        """测试短日期格式化 - 使用统一工具函数."""
        # 字符串日期
        result1 = format_date_short("2023-01-01T10:00:00.123456")
        assert result1 == "2023-01-01"

        # 短字符串
        result2 = format_date_short("2023-01-01")
        assert result2 == "2023-01-01"

        # 空值
        result3 = format_date_short("")
        assert result3 == ""

        result4 = format_date_short(None)
        assert result4 == ""
