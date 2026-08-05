"""UserProfileDomainData 单元测试.

测试用户画像领域数据的覆写(extract)和注入(inject_prompt)逻辑.
Mock PinnedMemoryRewriter, pinned_memory_block_service, cache.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.domain_data.user_profile_domain_data import UserProfileDomainData


def _make_conv(assistant_response: str = "reply", snapshot=None) -> Any:
    """构造模拟 ConversationData."""
    conv = MagicMock()
    conv.assistant_response = assistant_response
    conv.metadata = {"_messages_snapshot": snapshot}
    return conv


class TestOnConversationRound:
    """测试写入侧: 主模型覆写."""

    @pytest.mark.asyncio
    async def test_skip_when_no_conversation_data(self):
        """无 conversation_data 时直接跳过."""
        dd = UserProfileDomainData("u", "t", "agent-1")
        await dd.on_conversation_round(None)
        # 无异常即通过

    @pytest.mark.asyncio
    async def test_skip_when_no_snapshot(self):
        """无 messages 快照时跳过."""
        conv = _make_conv(snapshot=None)
        dd = UserProfileDomainData("u", "t", "agent-1")
        await dd.on_conversation_round(conv)
        # 无异常即通过

    @pytest.mark.asyncio
    async def test_rewrite_calls_pinned_memory_rewriter_with_local_mode(self):
        """有快照时调用 PinnedMemoryRewriter, mode='local'."""
        snapshot = [MagicMock()]
        conv = _make_conv(snapshot=snapshot)

        mock_result = MagicMock(needs_update=False, content="")
        mock_block_service = MagicMock()
        mock_block_service.get_content = AsyncMock(return_value="old memory")

        with (
            patch(
                "src.inference.content_analyzer.pinned_memory_rewriter."
                "PinnedMemoryRewriter",
            ) as mock_rewriter_cls,
            patch(
                "src.storage.service.create_pinned_memory_block_service",
                return_value=mock_block_service,
            ),
            patch(
                "src.agent.memory.rmw_state.get_rmw_lock",
            ) as mock_lock_cls,
        ):
            mock_rewriter_cls.return_value.rewrite = AsyncMock(return_value=mock_result)
            mock_lock = MagicMock()
            mock_lock.acquire = AsyncMock()
            mock_lock.release = MagicMock()
            mock_lock_cls.return_value = mock_lock

            dd = UserProfileDomainData("u", "t", "agent-1")
            await dd.on_conversation_round(conv)

        mock_block_service.get_content.assert_called_once()
        mock_rewriter_cls.return_value.rewrite.assert_called_once()
        call_kwargs = mock_rewriter_cls.return_value.rewrite.call_args[1]
        assert call_kwargs["mode"] == "local"


class TestInjectPrompt:
    """测试读取侧: 注入 system prompt."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_content(self):
        """无内容时返回空字符串."""
        with patch(
            "src.agent.memory.local_memory.cache.get_pinned_memory",
            return_value=None,
        ):
            dd = UserProfileDomainData("u", "t", "agent-1")
            result = await dd.inject_prompt()
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_formatted_from_cache(self):
        """缓存命中时返回带 XML 标签的内容."""
        with patch(
            "src.agent.memory.local_memory.cache.get_pinned_memory",
            return_value="用户: 张三, 喜欢简洁",
        ):
            dd = UserProfileDomainData("u", "t", "agent-1")
            result = await dd.inject_prompt()

        assert "以下是你需要长期记住的关键信息" in result
        assert "<pinned_memory>" in result
        assert "张三" in result

    @pytest.mark.asyncio
    async def test_falls_back_to_db_when_cache_miss(self):
        """缓存未命中时回退 DB, 并回填缓存."""
        mock_block_service = MagicMock()
        mock_block_service.get_formatted = AsyncMock(return_value="DB content")

        with (
            patch(
                "src.agent.memory.local_memory.cache.get_pinned_memory",
                return_value=None,
            ),
            patch(
                "src.agent.memory.local_memory.cache.set_pinned_memory",
            ) as mock_set_cache,
            patch(
                "src.storage.service.create_pinned_memory_block_service",
                return_value=mock_block_service,
            ),
        ):
            dd = UserProfileDomainData("u", "t", "agent-1")
            result = await dd.inject_prompt()

        assert "DB content" in result
        mock_set_cache.assert_called_once()
