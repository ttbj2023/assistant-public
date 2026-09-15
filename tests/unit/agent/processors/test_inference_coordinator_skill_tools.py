"""InferenceCoordinator skill 关联工具映射测试.

锁定行为: associated_tools 中的 internal tool 名 (如 wechat_publish)
不被特殊过滤, 与 external tool 名一样经 tool_manager.create_tools 创建,
保证 skill 可关联任意注册工具.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.processors.inference_coordinator import InferenceCoordinator


class TestBuildSkillToolMap:
    """_build_skill_tool_map 工具名委托行为."""

    @pytest.mark.asyncio
    async def test_internal_tool_name_delegated_to_create_tools(self) -> None:
        """internal 工具名 (wechat_publish) 应经 create_tools 创建而非被丢弃."""
        fake_tool = MagicMock()
        fake_tool.name = "wechat_publish"

        skill_bridge = MagicMock()
        skill_bridge.get_associated_tool_names.return_value = {
            "wechat_official_account": ["wechat_publish"],
        }
        tool_manager = MagicMock()
        tool_manager.create_tools = AsyncMock(return_value=[fake_tool])

        result = await InferenceCoordinator._build_skill_tool_map(
            skill_bridge,
            ["wechat_official_account"],
            tool_manager,
            "u1",
            "t1",
            agent_id="a1",
        )

        tool_manager.create_tools.assert_awaited_once_with(
            ["wechat_publish"], "u1", "t1", agent_id="a1"
        )
        assert "wechat_official_account" in result
        assert result["wechat_official_account"] == [fake_tool]
