"""轮次号异常跳跃检测集成测试.

灰盒: 真实 ProcessorOrchestrator + 真实 _detect_round_number_anomaly + 真实
ConversationService (get_latest_round_number 读真实 SQLite), 仅 Mock 外部 LLM.
验证轮次号跳跃 > 10 时记 warning 且不阻断主流程. 该检测逻辑此前任意层级零覆盖.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest


class TestRoundNumberAnomalyIntegration:
    """轮次号异常检测协作集成测试."""

    @pytest.mark.asyncio
    async def test_integration_round_jump_logs_warning_without_blocking(
        self, test_user, test_thread_id, caplog
    ):
        """轮次号跳跃 > 10 触发 warning, 且不阻断主流程.

        协作场景: ProcessorOrchestrator.process + _build_conversation_data +
            _detect_round_number_anomaly + 真实 ConversationService (读真实 SQLite)
        Mock 边界: 仅 Mock 外部 LLM (inference_coordinator.process_with_agent)
        验证重点:
            1. 预置 round=1, 以 round_number=100 处理 → caplog 含异常跳跃 warning
            2. 主流程不阻断, 正常返回响应
        业务价值: 轮次号异常多为上层 bug 的信号, 记录告警便于排查, 但不得影响对话
        """
        from src.agent.manager import get_agent_manager
        from src.storage.service import create_conversation_service

        agent_manager = get_agent_manager()
        agent = await agent_manager.get_agent("personal-assistant")
        await agent.initialize()

        # 预置 round=1 历史记录
        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id="personal-assistant"
        )
        await conv_service.create_conversation(
            user_message="历史消息",
            assistant_response="历史回复",
            user_id=test_user,
            thread_id=test_thread_id,
            agent_id="personal-assistant",
            round_number=1,
        )

        original_llm = agent._orchestrator.inference_coordinator

        async def mock_process_with_agent(*args, **kwargs):
            return "正常响应", {}

        caplog.set_level(logging.WARNING)

        with patch.object(
            original_llm,
            "process_with_agent",
            side_effect=mock_process_with_agent,
        ):
            response = await agent.process_message(
                message="测试跳跃",
                user_id=test_user,
                thread_id=test_thread_id,
                round_number=100,
            )

        # Assert: 主流程不阻断
        assert response == "正常响应"
        # Assert: 记录了异常跳跃 warning (gap = 100 - 1 = 99 > 10)
        assert "检测到轮次号异常跳跃" in caplog.text
        assert "99" in caplog.text

    @pytest.mark.asyncio
    async def test_integration_normal_round_increment_no_warning(
        self, test_user, test_thread_id, caplog
    ):
        """正常轮次递增 (gap <= 10) 不触发 warning (对照基线).

        协作场景: 同上, 但 round_number=2, 预置 round=1, gap=1 不触发
        Mock 边界: 仅 Mock 外部 LLM
        验证重点: caplog 不含异常跳跃 warning
        业务价值: 确认阈值判定正确, 正常递增不误报
        """
        from src.agent.manager import get_agent_manager
        from src.storage.service import create_conversation_service

        agent_manager = get_agent_manager()
        agent = await agent_manager.get_agent("personal-assistant")
        await agent.initialize()

        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id="personal-assistant"
        )
        await conv_service.create_conversation(
            user_message="历史消息",
            assistant_response="历史回复",
            user_id=test_user,
            thread_id=test_thread_id,
            agent_id="personal-assistant",
            round_number=1,
        )

        original_llm = agent._orchestrator.inference_coordinator

        async def mock_process_with_agent(*args, **kwargs):
            return "正常响应", {}

        caplog.set_level(logging.WARNING)

        with patch.object(
            original_llm,
            "process_with_agent",
            side_effect=mock_process_with_agent,
        ):
            await agent.process_message(
                message="测试正常",
                user_id=test_user,
                thread_id=test_thread_id,
                round_number=2,
            )

        assert "检测到轮次号异常跳跃" not in caplog.text
