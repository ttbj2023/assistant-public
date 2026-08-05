"""HealthDomainData 单元测试.

测试健康领域数据的调度分发逻辑: 审计轮走 audit, 非审计轮走常规提取.
Mock HealthDataBackgroundExtractor 和 run_audit/should_audit.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.agents_implementations.health_assistant.health_domain_data import (
    HealthDomainData,
)


def _make_conv(user_message: str) -> Any:
    """构造模拟 ConversationData."""
    conv = MagicMock()
    conv.user_message = user_message
    return conv


class TestOnConversationRound:
    """测试 on_conversation_round 调度分发."""

    @pytest.mark.asyncio
    async def test_audit_round_calls_run_audit(self):
        """审计轮次应调用 run_audit, 不调用常规提取."""
        conv = _make_conv("审计消息")

        with (
            patch(
                "src.agent.agents_implementations.health_assistant."
                "health_domain_data.should_audit",
                return_value=True,
            ),
            patch(
                "src.agent.agents_implementations.health_assistant."
                "health_domain_data.run_audit",
                new_callable=AsyncMock,
            ) as mock_audit,
            patch(
                "src.agent.agents_implementations.health_assistant."
                "health_domain_data.HealthDataBackgroundExtractor",
            ) as mock_extractor_cls,
        ):
            dd = HealthDomainData("u", "t", "health-assistant")
            await dd.on_conversation_round(conv, None, 10)

        mock_audit.assert_called_once_with(
            "u",
            "t",
            "health-assistant",
            10,
            user_message="审计消息",
            attachment_infos=None,
        )
        mock_extractor_cls.return_value.extract_from_conversation.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_audit_round_calls_extraction(self):
        """非审计轮次应调用常规提取, 不调用审计."""
        conv = _make_conv("常规消息")

        with (
            patch(
                "src.agent.agents_implementations.health_assistant."
                "health_domain_data.should_audit",
                return_value=False,
            ),
            patch(
                "src.agent.agents_implementations.health_assistant."
                "health_domain_data.run_audit",
                new_callable=AsyncMock,
            ) as mock_audit,
            patch(
                "src.agent.agents_implementations.health_assistant."
                "health_domain_data.HealthDataBackgroundExtractor",
            ) as mock_extractor_cls,
        ):
            mock_extractor_cls.return_value.extract_from_conversation = AsyncMock()
            dd = HealthDomainData("u", "t", "health-assistant")
            await dd.on_conversation_round(conv, [{"id": 1}], 5)

        mock_audit.assert_not_called()
        mock_extractor_cls.return_value.extract_from_conversation.assert_called_once_with(
            user_message="常规消息",
            attachment_infos=[{"id": 1}],
            round_number=5,
        )

    @pytest.mark.asyncio
    async def test_skip_when_no_conversation_data(self):
        """无对话数据时直接跳过."""
        with (
            patch(
                "src.agent.agents_implementations.health_assistant."
                "health_domain_data.should_audit",
                return_value=True,
            ) as mock_should,
            patch(
                "src.agent.agents_implementations.health_assistant."
                "health_domain_data.run_audit",
                new_callable=AsyncMock,
            ) as mock_audit,
            patch(
                "src.agent.agents_implementations.health_assistant."
                "health_domain_data.HealthDataBackgroundExtractor",
            ) as mock_extractor_cls,
        ):
            dd = HealthDomainData("u", "t", "health-assistant")
            await dd.on_conversation_round(None, None, 10)

        mock_should.assert_not_called()
        mock_audit.assert_not_called()
        mock_extractor_cls.return_value.extract_from_conversation.assert_not_called()

    @pytest.mark.asyncio
    async def test_round_zero_does_not_trigger_audit(self):
        """round_number=0 时 effective_round=0, 不触发审计."""
        conv = _make_conv("x")

        with (
            patch(
                "src.agent.agents_implementations.health_assistant."
                "health_domain_data.should_audit",
                return_value=False,
            ) as mock_should,
            patch(
                "src.agent.agents_implementations.health_assistant."
                "health_domain_data.HealthDataBackgroundExtractor",
            ) as mock_extractor_cls,
        ):
            mock_extractor_cls.return_value.extract_from_conversation = AsyncMock()
            dd = HealthDomainData("u", "t", "health-assistant")
            await dd.on_conversation_round(conv, None, None)

        mock_should.assert_not_called()
        mock_extractor_cls.return_value.extract_from_conversation.assert_called_once()

    @pytest.mark.asyncio
    async def test_inject_prompt_returns_empty(self):
        """Phase 1: inject_prompt 返回空字符串."""
        dd = HealthDomainData("u", "t", "health-assistant")
        assert await dd.inject_prompt() == ""
