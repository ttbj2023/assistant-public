"""InsightsDomainData 单元测试.

测试经验洞察领域数据的覆写(extract)和注入(inject_prompt)逻辑.
与 UserProfileDomainData 的唯一差异: mode='simple', namespace='memory'.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.domain_data.insights_domain_data import InsightsDomainData


def _make_conv(assistant_response: str = "reply", snapshot=None) -> Any:
    """构造模拟 ConversationData."""
    conv = MagicMock()
    conv.assistant_response = assistant_response
    conv.metadata = {"_messages_snapshot": snapshot}
    return conv


class TestOnConversationRound:
    """测试写入侧: 主模型覆写."""

    @pytest.mark.asyncio
    async def test_skip_when_no_snapshot(self):
        """无 messages 快照时跳过."""
        conv = _make_conv(snapshot=None)
        dd = InsightsDomainData("u", "t", "agent-1")
        await dd.on_conversation_round(conv)

    @pytest.mark.asyncio
    async def test_rewrite_calls_pinned_memory_rewriter_with_simple_mode(self):
        """有快照时调用 PinnedMemoryRewriter, mode='simple'."""
        snapshot = [MagicMock()]
        conv = _make_conv(snapshot=snapshot)

        mock_result = MagicMock(needs_update=False, content="")
        mock_block_service = MagicMock()
        mock_block_service.get_content = AsyncMock(return_value="old insights")

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

            dd = InsightsDomainData("u", "t", "agent-1")
            await dd.on_conversation_round(conv)

        call_kwargs = mock_rewriter_cls.return_value.rewrite.call_args[1]
        assert call_kwargs["mode"] == "simple"


class TestInjectPrompt:
    """测试读取侧: 注入 system prompt."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_content(self):
        """无内容时返回空字符串."""
        with patch(
            "src.agent.memory.local_memory.cache.get_pinned_memory",
            return_value=None,
        ):
            dd = InsightsDomainData("u", "t", "agent-1")
            result = await dd.inject_prompt()
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_formatted_from_cache(self):
        """缓存命中时返回带 XML 标签的内容."""
        with patch(
            "src.agent.memory.local_memory.cache.get_pinned_memory",
            return_value="用户偏好简洁的写作风格",
        ):
            dd = InsightsDomainData("u", "t", "agent-1")
            result = await dd.inject_prompt()

        assert "<pinned_memory>" in result
        assert "简洁的写作风格" in result
