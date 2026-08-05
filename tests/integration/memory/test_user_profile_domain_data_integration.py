"""UserProfileDomainData 用户画像覆写集成测试.

验证 UserProfileDomainData.on_conversation_round 在 needs_update=True 时的真实写入
与缓存清理路径, 取代单元测试中 patch 4 个协作对象只断言 mock 被调用的脚手架测试
(原 test_user_profile_domain_data.py:81-125).

测试策略: 灰盒 - 真实 UserProfileDomainData + 真实 PinnedMemoryBlockService + 真实
SQLite, 仅 Mock LLM 边界 (invoke_with_fallback) 控制 needs_update 返回. 验证:
1. needs_update=True → pinned_memory_block 表真实写入新 content
2. needs_update=True → SplittableMemoryCache 中 pinned 缓存被真实清除
3. 无 messages 快照 → 跳过覆写 (invoke_with_fallback 不被调用)
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import HumanMessage

from src.agent.domain_data.user_profile_domain_data import UserProfileDomainData
from src.storage.models.conversation import ConversationData
from src.storage.service.service_factory import create_pinned_memory_block_service

_AGENT_ID = "test-agent"
_NEW_MEMORY = "新画像: 用户偏好简洁回复, 常用 Python"


def _make_conversation(
    test_user: str, test_thread_id: str, *, with_snapshot: bool = True
) -> ConversationData:
    """构造带 messages 快照的 ConversationData."""
    metadata: dict[str, list[HumanMessage]] = {}
    if with_snapshot:
        metadata["_messages_snapshot"] = [HumanMessage(content="我喜欢简洁的回复")]
    return ConversationData(
        user_id=test_user,
        thread_id=test_thread_id,
        user_message="我喜欢简洁的回复",
        assistant_response="好的, 我会保持简洁",
        round_number=1,
        timestamp=datetime.now(UTC),
        agent_id=_AGENT_ID,
        metadata=metadata,
    )


def _mock_llm_needs_update(content: str) -> AsyncMock:
    """构造 invoke_with_fallback 的 Mock, 返回 needs_update=True 的 JSON."""
    import json

    mock_resp = SimpleNamespace(
        content=json.dumps({"needs_update": True, "content": content})
    )
    return AsyncMock(return_value=mock_resp)


@pytest.mark.integration
class TestUserProfileDomainDataIntegration:
    """UserProfileDomainData 真实写入与缓存清理验证."""

    @pytest.mark.asyncio
    async def test_needs_update_writes_to_pinned_memory_block_table(
        self, test_user: str, test_thread_id: str
    ):
        """needs_update=True 应真实覆写 pinned_memory_block 表."""
        dd = UserProfileDomainData(test_user, test_thread_id, _AGENT_ID)
        conv = _make_conversation(test_user, test_thread_id)

        with patch(
            "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
            _mock_llm_needs_update(_NEW_MEMORY),
        ):
            await dd.on_conversation_round(conv)

        # 直接查真实 DB 验证写入 (非 mock 调用断言)
        block_service = await create_pinned_memory_block_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        stored = await block_service.get_content(test_user, test_thread_id)
        assert stored == _NEW_MEMORY

    @pytest.mark.asyncio
    async def test_needs_update_clears_pinned_memory_cache(
        self, test_user: str, test_thread_id: str
    ):
        """needs_update=True 应清除 SplittableMemoryCache 中的 pinned 缓存."""
        from src.agent.memory.local_memory.cache import (
            get_pinned_memory,
            set_pinned_memory,
        )

        # 预热缓存, 模拟上一轮读取后回填
        set_pinned_memory(test_user, test_thread_id, "旧画像缓存", agent_id=_AGENT_ID)
        assert (
            get_pinned_memory(test_user, test_thread_id, agent_id=_AGENT_ID)
            == "旧画像缓存"
        )

        dd = UserProfileDomainData(test_user, test_thread_id, _AGENT_ID)
        conv = _make_conversation(test_user, test_thread_id)

        with patch(
            "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
            _mock_llm_needs_update(_NEW_MEMORY),
        ):
            await dd.on_conversation_round(conv)

        # 缓存应被真实清除 (非 mock 调用断言)
        assert get_pinned_memory(test_user, test_thread_id, agent_id=_AGENT_ID) is None

    @pytest.mark.asyncio
    async def test_no_messages_snapshot_skips_rewrite(
        self, test_user: str, test_thread_id: str
    ):
        """无 messages 快照应跳过覆写, LLM 不被调用."""
        dd = UserProfileDomainData(test_user, test_thread_id, _AGENT_ID)
        conv = _make_conversation(test_user, test_thread_id, with_snapshot=False)

        with patch(
            "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
        ) as mock_invoke:
            await dd.on_conversation_round(conv)

        mock_invoke.assert_not_called()
