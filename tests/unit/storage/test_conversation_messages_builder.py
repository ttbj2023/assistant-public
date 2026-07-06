"""conversation_messages_builder 单元测试.

测试职责: 验证 ConversationIndex 到原生 LangChain messages 的转换逻辑.
测试范围: 空列表、单轮、多轮、含空字段、顺序保持.
Mock策略: 无 Mock, 直接构造 ConversationIndex 实例.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from src.storage.models.conversation import ConversationIndex
from src.storage.service.conversation_messages_builder import (
    build_messages_from_conversations,
)


def _make_conv(
    round_number: int,
    user_message: str = "",
    assistant_response: str = "",
) -> ConversationIndex:
    """构造一个最小可用的 ConversationIndex 实例 (不绑定 session)."""
    return ConversationIndex(
        round_number=round_number,
        user_message=user_message,
        assistant_response=assistant_response,
    )


class TestBuildMessagesFromConversations:
    """build_messages_from_conversations 转换逻辑测试."""

    def test_empty_list_returns_empty(self) -> None:
        """空列表应返回空 messages 列表."""
        result = build_messages_from_conversations([])
        assert result == []

    def test_single_round_produces_human_ai_pair(self) -> None:
        """单轮应生成一对 HumanMessage + AIMessage."""
        conversations = [
            _make_conv(1, "你好", "你好!有什么可以帮您?"),
        ]
        result = build_messages_from_conversations(conversations)

        assert len(result) == 2
        assert isinstance(result[0], HumanMessage)
        assert isinstance(result[1], AIMessage)
        assert result[0].content == "你好"
        assert result[1].content == "你好!有什么可以帮您?"

    def test_multiple_rounds_preserve_order_and_alternation(self) -> None:
        """多轮应保持 round_number 升序, Human/AI 严格交替."""
        conversations = [
            _make_conv(1, "u1", "a1"),
            _make_conv(2, "u2", "a2"),
            _make_conv(3, "u3", "a3"),
        ]
        result = build_messages_from_conversations(conversations)

        assert len(result) == 6
        for i, msg in enumerate(result):
            if i % 2 == 0:
                assert isinstance(msg, HumanMessage), f"位置 {i} 应为 HumanMessage"
            else:
                assert isinstance(msg, AIMessage), f"位置 {i} 应为 AIMessage"

        assert result[0].content == "u1"
        assert result[1].content == "a1"
        assert result[4].content == "u3"
        assert result[5].content == "a3"

    def test_empty_string_fields_still_produce_messages(self) -> None:
        """空字符串字段应仍生成对应 message, 维持 Human/AI 交替结构."""
        conversations = [_make_conv(1, "", "")]
        result = build_messages_from_conversations(conversations)

        assert len(result) == 2
        assert isinstance(result[0], HumanMessage)
        assert isinstance(result[1], AIMessage)
        assert result[0].content == ""
        assert result[1].content == ""

    def test_input_list_not_mutated(self) -> None:
        """转换应为纯函数, 不修改输入列表."""
        conversations = [_make_conv(1, "u", "a")]
        original_len = len(conversations)
        build_messages_from_conversations(conversations)
        assert len(conversations) == original_len

    def test_does_not_sort_input(self) -> None:
        """转换器不负责排序, 应按输入原顺序输出 (调用方负责排序)."""
        conversations = [
            _make_conv(3, "u3", "a3"),
            _make_conv(1, "u1", "a1"),
        ]
        result = build_messages_from_conversations(conversations)

        assert len(result) == 4
        assert result[0].content == "u3"
        assert result[1].content == "a3"
        assert result[2].content == "u1"
        assert result[3].content == "a1"
