"""HealthAssistantAgent单元测试.

测试健康管理Agent的核心业务逻辑: 后台任务调度和审计轮次分发.
Mock所有外部依赖（HealthDataBackgroundExtractor, run_audit, should_audit）。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.agents_implementations.health_assistant.main import (
    HealthAssistantAgent,
)
from src.config.agent_config import AgentConfig, AgentMemoryConfig


@pytest.fixture
def mock_agent_config() -> AgentConfig:
    """创建Mock Agent配置."""
    return AgentConfig(
        agent_id="health-assistant",
        name="Health Assistant",
        description="健康助手",
        system_prompt="你是健康助手",
        model_id="local:qwen3.5:9b",
        llm_config={"temperature": 0.7, "max_tokens": 4000},
        tools=[],
        memory=AgentMemoryConfig(),
    )


@pytest.fixture
def health_assistant(mock_agent_config: AgentConfig) -> HealthAssistantAgent:
    """创建HealthAssistantAgent实例."""
    return HealthAssistantAgent(mock_agent_config)


class TestScheduleHealthDataExtraction:
    """测试后台健康数据提取调度."""

    @pytest.mark.asyncio
    async def test_schedule_extraction_should_create_task_when_event_loop_running(
        self, health_assistant
    ):
        """测试调度提取：事件循环运行时应创建后台任务."""
        # Arrange
        created_tasks = []

        async def fake_run_in_loop():
            # 模拟create_task行为
            coro = AsyncMock()()
            task = asyncio_task = MagicMock()
            task.add_done_callback = MagicMock()
            created_tasks.append(task)
            return task

        # Act
        with patch(
            "src.agent.agents_implementations.health_assistant.main.HealthDataBackgroundExtractor"
        ) as mock_extractor_cls, patch("asyncio.get_running_loop") as mock_get_loop:
            mock_extractor_cls.return_value.extract_from_conversation = AsyncMock()
            mock_loop = MagicMock()

            def fake_create_task(coro):
                task = MagicMock()
                # 立即关闭协程避免警告
                coro.close()
                return task

            mock_loop.create_task = fake_create_task
            mock_get_loop.return_value = mock_loop

            health_assistant._schedule_health_data_extraction(
                user_message="我体重70kg",
                user_id="test_user",
                thread_id="test_thread",
                round_number=1,
            )

        # Assert
        mock_get_loop.assert_called_once()

    def test_schedule_extraction_should_warn_when_no_event_loop(
        self, health_assistant, caplog
    ):
        """测试调度提取：无事件循环时应记录警告."""
        # Act
        with patch(
            "src.agent.agents_implementations.health_assistant.main.HealthDataBackgroundExtractor"
        ), patch(
            "asyncio.get_running_loop",
            side_effect=RuntimeError("no running loop"),
        ):
            health_assistant._schedule_health_data_extraction(
                user_message="x",
                user_id="test_user",
                thread_id="test_thread",
            )

        # Assert
        assert any("无法获取事件循环" in r.message for r in caplog.records)


class TestScheduleHealthDataAudit:
    """测试后台健康数据审计调度."""

    def test_schedule_audit_should_warn_when_no_event_loop(
        self, health_assistant, caplog
    ):
        """测试调度审计：无事件循环时应记录警告."""
        # Act
        with patch(
            "asyncio.get_running_loop",
            side_effect=RuntimeError("no loop"),
        ):
            health_assistant._schedule_health_data_audit(
                user_id="u",
                thread_id="t",
                current_round=10,
            )

        # Assert
        assert any("无法获取事件循环" in r.message for r in caplog.records)


class TestDispatchHealthData:
    """测试健康数据调度分发逻辑."""

    def _make_conv(self, user_message: str) -> Any:
        """构造模拟ConversationData."""
        conv = MagicMock()
        conv.user_message = user_message
        return conv

    def test_dispatch_should_call_audit_on_audit_round(
        self, health_assistant
    ):
        """测试调度：审计轮次时应调用审计任务."""
        # Arrange
        conv = self._make_conv("消息")
        kwargs = {"round_number": 10}

        # Act
        with patch(
            "src.agent.agents_implementations.health_assistant.main.should_audit",
            return_value=True,
        ), patch.object(
            health_assistant, "_schedule_health_data_audit"
        ) as mock_audit, patch.object(
            health_assistant, "_schedule_health_data_extraction"
        ) as mock_extract:
            health_assistant._dispatch_health_data(
                conversation_data=conv,
                user_id="u",
                thread_id="t",
                attachment_infos=None,
                kwargs=kwargs,
            )

        # Assert
        mock_audit.assert_called_once()
        mock_extract.assert_not_called()
        # 验证传递了user_message
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs["user_message"] == "消息"
        assert call_kwargs["current_round"] == 10

    def test_dispatch_should_call_extraction_on_non_audit_round(
        self, health_assistant
    ):
        """测试调度：非审计轮次应调用常规提取."""
        # Arrange
        conv = self._make_conv("常规消息")
        kwargs = {"round_number": 5}

        # Act
        with patch(
            "src.agent.agents_implementations.health_assistant.main.should_audit",
            return_value=False,
        ), patch.object(
            health_assistant, "_schedule_health_data_audit"
        ) as mock_audit, patch.object(
            health_assistant, "_schedule_health_data_extraction"
        ) as mock_extract:
            health_assistant._dispatch_health_data(
                conversation_data=conv,
                user_id="u",
                thread_id="t",
                attachment_infos=None,
                kwargs=kwargs,
            )

        # Assert
        mock_extract.assert_called_once()
        mock_audit.assert_not_called()

    def test_dispatch_should_skip_when_no_conversation_data(
        self, health_assistant
    ):
        """测试调度：无对话数据时应跳过."""
        # Act
        with patch(
            "src.agent.agents_implementations.health_assistant.main.should_audit",
            return_value=False,
        ), patch.object(
            health_assistant, "_schedule_health_data_extraction"
        ) as mock_extract:
            health_assistant._dispatch_health_data(
                conversation_data=None,
                user_id="u",
                thread_id="t",
                attachment_infos=None,
                kwargs={"round_number": 5},
            )

        # Assert
        mock_extract.assert_not_called()

    def test_dispatch_should_skip_audit_when_no_conversation_data_even_on_audit_round(
        self, health_assistant
    ):
        """测试调度：审计轮次但无对话数据时也应跳过审计."""
        # Act
        with patch(
            "src.agent.agents_implementations.health_assistant.main.should_audit",
            return_value=True,
        ), patch.object(
            health_assistant, "_schedule_health_data_audit"
        ) as mock_audit:
            health_assistant._dispatch_health_data(
                conversation_data=None,
                user_id="u",
                thread_id="t",
                attachment_infos=None,
                kwargs={"round_number": 10},
            )

        # Assert
        mock_audit.assert_not_called()

    def test_dispatch_should_use_zero_round_when_kwargs_missing_round(
        self, health_assistant
    ):
        """测试调度：kwargs缺少round_number时按0处理, should_audit不会触发."""
        # Arrange
        conv = self._make_conv("x")

        # Act
        with patch(
            "src.agent.agents_implementations.health_assistant.main.should_audit",
            return_value=False,
        ) as mock_should_audit, patch.object(
            health_assistant, "_schedule_health_data_extraction"
        ) as mock_extract:
            health_assistant._dispatch_health_data(
                conversation_data=conv,
                user_id="u",
                thread_id="t",
                attachment_infos=None,
                kwargs={},
            )

        # Assert
        # effective_round=0 -> 不调用should_audit (effective_round > 0失败)
        mock_should_audit.assert_not_called()
        mock_extract.assert_called_once()


class TestHooks:
    """测试Agent生命周期钩子."""

    @pytest.mark.asyncio
    async def test_post_process_hook_should_dispatch(
        self, health_assistant
    ):
        """测试后处理钩子：应调用调度逻辑."""
        # Arrange
        conv = MagicMock()
        conv.user_message = "msg"

        # Act & Assert
        with patch.object(
            health_assistant, "_dispatch_health_data"
        ) as mock_dispatch:
            await health_assistant._post_process_hook(
                result="response",
                conversation_data=conv,
                user_id="u",
                thread_id="t",
                attachment_infos=None,
                kwargs={},
            )

        mock_dispatch.assert_called_once()

    def test_pre_stream_hook_should_cache_pending_state(
        self, health_assistant
    ):
        """测试流式前置钩子：应缓存image_datas和attachment_infos."""
        # Arrange
        images = [{"url": "x"}]
        attachments = [{"id": 1}]

        # Act
        health_assistant._pre_stream_hook(
            image_datas=images,
            attachment_infos=attachments,
            kwargs={},
        )

        # Assert
        assert health_assistant._pending_image_datas is images
        assert health_assistant._pending_attachment_infos is attachments

    @pytest.mark.asyncio
    async def test_post_finalize_hook_should_dispatch_and_clear(
        self, health_assistant
    ):
        """测试finalize钩子：应调度并清理pending状态."""
        # Arrange
        health_assistant._pending_attachment_infos = [{"id": 1}]
        health_assistant._pending_image_datas = [{"url": "x"}]
        conv = MagicMock()

        # Act
        with patch.object(
            health_assistant, "_dispatch_health_data"
        ) as mock_dispatch:
            await health_assistant._post_finalize_hook(
                response="resp",
                conversation_data=conv,
                user_id="u",
                thread_id="t",
                kwargs={},
            )

        # Assert
        mock_dispatch.assert_called_once()
        # 验证传给dispatch的是pending状态
        call_kwargs = mock_dispatch.call_args[1]
        assert call_kwargs["attachment_infos"] == [{"id": 1}]
        # 验证清理
        assert health_assistant._pending_image_datas is None
        assert health_assistant._pending_attachment_infos is None

    def test_cleanup_hook_should_clear_pending_state(self, health_assistant):
        """测试清理钩子：应清空pending状态."""
        # Arrange
        health_assistant._pending_image_datas = [{"x": 1}]
        health_assistant._pending_attachment_infos = [{"y": 2}]

        # Act
        health_assistant._cleanup_hook()

        # Assert
        assert health_assistant._pending_image_datas is None
        assert health_assistant._pending_attachment_infos is None
