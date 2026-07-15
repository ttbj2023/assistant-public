"""replay_index_arc 脚本 backfill_one 守卫的单元测试.

验证 simple 模式 agent 不被弧短语回填处理(显式 memory.type 守卫,
而非依赖 summary 为空的隐式行为).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.config.agent_config import AgentConfig, AgentMemoryConfig


class TestBackfillOneMemoryTypeGuard:
    """backfill_one 的 memory.type 守卫行为."""

    @pytest.mark.asyncio
    async def test_simple_memory_agent_skipped(self) -> None:
        """simple 模式 agent 跳过弧短语回填, 不触发后续 DB 操作."""
        from scripts.replay_index_arc import backfill_one

        mock_cfg = AgentConfig(
            agent_id="thought-assistant",
            memory=AgentMemoryConfig(type="simple"),
        )

        with (
            patch(
                "src.agent.factory.AgentFactory.load_agent_config",
                new=AsyncMock(return_value=mock_cfg),
            ),
            patch(
                "src.storage.service.create_conversation_service",
                new=AsyncMock(),
            ) as mock_create_svc,
        ):
            result = await backfill_one(
                "test_user", "main", "thought-assistant", 0.5, 60, False
            )

        assert result == {"rounds": 0, "runs": 0, "frozen": 0, "skipped": 0}
        mock_create_svc.assert_not_called()
