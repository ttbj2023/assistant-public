"""SimpleMemoryProcessor 单元测试.

覆盖范围:
- _convert_chat_messages: system 过滤、user/assistant 转换、多模态保留
- _convert_content: str / list 内容块转换
- build_messages_context: 历史透传 + extension + current_content 组装
- get_prompt_hint: 格式描述
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agent.processors.simple_memory_processor import (
    SimpleMemoryProcessor,
    _convert_chat_messages,
    _convert_content,
)


class TestConvertContent:
    def test_str_content_passthrough(self) -> None:
        assert _convert_content("hello") == "hello"

    def test_text_block(self) -> None:
        result = _convert_content([{"type": "text", "text": "hi"}])
        assert result == [{"type": "text", "text": "hi"}]

    def test_image_url_block_preserved(self) -> None:
        result = _convert_content([
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}
        ])
        assert result == [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}
        ]

    def test_mixed_blocks(self) -> None:
        result = _convert_content([
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "http://x/y.png"}},
        ])
        assert len(result) == 2
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "image_url"

    def test_empty_text_block_skipped(self) -> None:
        result = _convert_content([{"type": "text", "text": ""}])
        assert result == []

    def test_non_dict_non_list_returns_str(self) -> None:
        assert _convert_content(123) == "123"


class TestConvertChatMessages:
    def test_system_message_filtered(self) -> None:
        """后端权威: 前端 system 消息必须被过滤掉."""
        msgs = [
            {"role": "system", "content": "前端注入的 system"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好啊"},
        ]
        result = _convert_chat_messages(msgs)
        assert len(result) == 2
        assert isinstance(result[0], HumanMessage)
        assert isinstance(result[1], AIMessage)

    def test_user_assistant_conversion(self) -> None:
        msgs = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
        ]
        result = _convert_chat_messages(msgs)
        assert isinstance(result[0], HumanMessage)
        assert result[0].content == "问题"
        assert isinstance(result[1], AIMessage)
        assert result[1].content == "回答"

    def test_multimodal_preserved(self) -> None:
        """多模态 image_url 内容块应原样保留."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这是什么"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,xx"},
                    },
                ],
            }
        ]
        result = _convert_chat_messages(msgs)
        assert len(result) == 1
        assert isinstance(result[0], HumanMessage)
        assert isinstance(result[0].content, list)
        assert result[0].content[0]["type"] == "text"
        assert result[0].content[1]["type"] == "image_url"

    def test_none_returns_empty(self) -> None:
        assert _convert_chat_messages(None) == []

    def test_empty_returns_empty(self) -> None:
        assert _convert_chat_messages([]) == []

    def test_unknown_role_skipped(self) -> None:
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {"role": "tool", "content": "t"},
        ]
        result = _convert_chat_messages(msgs)
        assert len(result) == 1
        assert isinstance(result[0], HumanMessage)


class TestSimpleMemoryProcessor:
    @pytest.fixture
    def processor(self) -> SimpleMemoryProcessor:
        return SimpleMemoryProcessor(None)

    def test_get_prompt_hint_describes_tags(
        self, processor: SimpleMemoryProcessor
    ) -> None:
        hint = processor.get_prompt_hint()
        assert "long_term_memory" in hint
        assert "user_input" in hint

    @pytest.mark.asyncio
    async def test_build_messages_context_passthrough(
        self,
        processor: SimpleMemoryProcessor,
        test_user: str,
        test_thread_id: str,
    ) -> None:
        """历史透传 + system 过滤 + extension 注入."""
        agent_config = type(
            "C", (), {"agent_id": "thought-assistant", "id": "thought-assistant"}
        )()
        processor_config = {
            "agent_config": agent_config,
            "chat_messages": [
                {"role": "system", "content": "前端 system"},
                {"role": "user", "content": "历史问题"},
                {"role": "assistant", "content": "历史回答"},
            ],
        }

        mock_manager = AsyncMock()
        mock_manager.get_memory_for_injection.return_value = (
            "<long_term_memory>\n## 用户偏好\n回复简洁\n</long_term_memory>"
        )
        with patch(
            "src.agent.memory.simple_memory.manager.SimpleMemoryManager",
            return_value=mock_manager,
        ):
            ctx = await processor.build_messages_context(
                user_input="当前输入",
                user_id=test_user,
                thread_id=test_thread_id,
                processor_config=processor_config,
            )

        # system 被过滤, 历史剩 2 条
        assert len(ctx.history_messages) == 2
        assert isinstance(ctx.history_messages[0], HumanMessage)
        assert isinstance(ctx.history_messages[1], AIMessage)
        # extension 注入
        assert "long_term_memory" in ctx.system_prompt_extension
        # current_content 含 user_input
        assert "当前输入" in ctx.current_content
        assert "<user_input>" in ctx.current_content

    @pytest.mark.asyncio
    async def test_build_messages_context_first_turn_empty_history(
        self,
        processor: SimpleMemoryProcessor,
        test_user: str,
        test_thread_id: str,
    ) -> None:
        """首轮: 无历史(chat_messages 为 None), extension 可能为空."""
        agent_config = type("C", (), {"agent_id": "ta", "id": "ta"})()
        processor_config = {"agent_config": agent_config, "chat_messages": None}

        mock_manager = AsyncMock()
        mock_manager.get_memory_for_injection.return_value = ""
        with patch(
            "src.agent.memory.simple_memory.manager.SimpleMemoryManager",
            return_value=mock_manager,
        ):
            ctx = await processor.build_messages_context(
                user_input="第一条",
                user_id=test_user,
                thread_id=test_thread_id,
                processor_config=processor_config,
            )

        assert ctx.history_messages == []
        assert ctx.system_prompt_extension == ""
        assert "第一条" in ctx.current_content

    @pytest.mark.asyncio
    async def test_build_messages_context_missing_agent_config_raises(
        self,
        processor: SimpleMemoryProcessor,
        test_user: str,
        test_thread_id: str,
    ) -> None:
        with pytest.raises(ValueError, match="agent_config"):
            await processor.build_messages_context(
                user_input="x",
                user_id=test_user,
                thread_id=test_thread_id,
                processor_config={},
            )

    @pytest.mark.asyncio
    async def test_get_or_create_conversation_memory(
        self,
        processor: SimpleMemoryProcessor,
        test_user: str,
        test_thread_id: str,
    ) -> None:
        agent_config = type("C", (), {"agent_id": "ta"})()
        core = await processor.get_or_create_conversation_memory(
            test_user, test_thread_id, agent_config
        )
        assert core.agent_id == "ta"
