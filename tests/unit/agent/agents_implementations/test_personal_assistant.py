"""个人助手Agent实现单元测试（修复版）.

测试职责: 验证PersonalAssistantAgent的核心功能逻辑
测试范围: Agent初始化、配置处理、消息处理
Mock策略: Mock外部依赖（处理器协调器），保留Agent业务逻辑
测试价值: 确保个人助手Agent实现的稳定性和可靠性
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.agent.agents_implementations.personal_assistant.main import (
    PersonalAssistantAgent,
)
from src.config.agent_config import AgentConfig
from tests.decorators import quick_test


class TestPersonalAssistantAgent:
    """PersonalAssistantAgent核心测试."""

    @pytest.fixture
    def mock_agent_config(self) -> AgentConfig:
        """创建Mock Agent配置."""
        from src.config.agent_config import AgentMemoryConfig

        return AgentConfig(
            agent_id="personal-assistant",
            name="Personal Assistant",
            description="个人助手Agent",
            system_prompt="你是一个有用的助手",
            model_id="local:qwen3.5:9b",  # 更新为系统中实际存在的模型
            llm_config={"temperature": 0.7, "max_tokens": 4000},
            tools=["todo_tool", "memory_tool"],
            memory=AgentMemoryConfig(),
        )

    @pytest.fixture
    def mock_app_config(self) -> dict:
        """创建Mock应用配置."""
        return {
            "model": {"llm": "local:qwen3.5:9b"},  # 更新为系统中实际存在的模型
            "cache": {"ttl": 3600},
            "storage": {"type": "sqlite"},
        }

    @pytest.fixture
    def personal_assistant(
        self, mock_agent_config: AgentConfig
    ) -> PersonalAssistantAgent:
        """创建PersonalAssistantAgent实例."""
        agent = PersonalAssistantAgent(mock_agent_config)
        return agent

    @pytest.mark.asyncio
    @quick_test
    async def test_initialize_success(self, mock_agent_config: AgentConfig) -> None:
        """测试成功初始化."""
        with patch(
            "src.agent.agents_implementations.base_orchestrator_agent.ProcessorOrchestrator"
        ) as mock_orchestrator_class:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.initialize = AsyncMock()
            mock_orchestrator_class.return_value = mock_orchestrator

            # 在Mock生效的环境下创建agent
            personal_assistant = PersonalAssistantAgent(mock_agent_config)
            await personal_assistant.initialize()

            assert personal_assistant._initialized is True
            assert personal_assistant._orchestrator == mock_orchestrator
            mock_orchestrator_class.assert_called_once()
            mock_orchestrator.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_orchestrator_failure(
        self, mock_agent_config: AgentConfig
    ) -> None:
        """测试处理器协调器初始化失败."""
        with patch(
            "src.agent.agents_implementations.base_orchestrator_agent.ProcessorOrchestrator",
            side_effect=Exception("Orchestrator init failed"),
        ):
            with pytest.raises(Exception, match="Orchestrator init failed"):
                # 在Mock生效的环境下创建agent
                personal_assistant = PersonalAssistantAgent(mock_agent_config)
                await personal_assistant.initialize()

    @pytest.mark.asyncio
    @quick_test
    async def test_double_initialize(self, mock_agent_config: AgentConfig) -> None:
        """测试重复初始化."""
        with patch(
            "src.agent.agents_implementations.base_orchestrator_agent.ProcessorOrchestrator"
        ) as mock_orchestrator_class:
            mock_orchestrator = AsyncMock()
            mock_orchestrator_class.return_value = mock_orchestrator

            # 在Mock生效的环境下创建agent
            personal_assistant = PersonalAssistantAgent(mock_agent_config)

            # 第一次初始化
            await personal_assistant.initialize()
            assert personal_assistant._initialized is True

            # 第二次初始化应该被跳过
            await personal_assistant.initialize()

            # 确保只调用了一次构造函数
            assert mock_orchestrator_class.call_count == 1

    @pytest.mark.asyncio
    @quick_test
    async def test_process_message_success(
        self,
        personal_assistant: PersonalAssistantAgent,
        test_user: str,
        test_thread_id: str,
    ) -> None:
        """测试成功处理消息."""
        # 先初始化
        mock_orchestrator = AsyncMock()
        # process方法应该返回元组 (result, metadata)
        mock_orchestrator.process.return_value = (
            "AI回复内容",
            {"status": "success"},
            None,
        )
        personal_assistant._orchestrator = mock_orchestrator
        personal_assistant._initialized = True

        message = "你好，请介绍一下你自己"
        response = await personal_assistant.process_message(
            message, test_user, test_thread_id
        )

        assert response == "AI回复内容"
        mock_orchestrator.process.assert_called_once()

        # 检查传递给orchestrator的参数
        call_args = mock_orchestrator.process.call_args
        args, kwargs = call_args
        assert args[0] == message  # user_input
        assert args[1] == test_user  # user_id
        assert args[2] == test_thread_id  # thread_id

        # 检查processor_config是否包含正确的配置
        processor_config = kwargs.get("processor_config") if len(args) <= 3 else args[3]
        assert processor_config is not None
        assert "agent_config" in processor_config

    @pytest.mark.asyncio
    async def test_process_message_not_initialized(
        self, mock_agent_config: AgentConfig, test_user: str, test_thread_id: str
    ) -> None:
        """测试未初始化时处理消息."""
        # Mock初始化过程，但在测试时确保orchestrator未设置
        with patch(
            "src.agent.agents_implementations.base_orchestrator_agent.ProcessorOrchestrator"
        ) as mock_orchestrator_class:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.process.return_value = ("AI回复", None, None)
            mock_orchestrator_class.return_value = mock_orchestrator
            mock_orchestrator.initialize = AsyncMock()

            message = "测试消息"

            # 在Mock生效的环境下创建agent
            personal_assistant = PersonalAssistantAgent(mock_agent_config)

            # 由于orchestrator被正确初始化，这应该正常工作
            await personal_assistant.process_message(message, test_user, test_thread_id)

            assert personal_assistant._initialized is True

    @pytest.mark.asyncio
    async def test_process_message_orchestrator_not_set(
        self,
        personal_assistant: PersonalAssistantAgent,
        test_user: str,
        test_thread_id: str,
    ) -> None:
        """测试orchestrator未设置时的处理."""
        # 设置initialized为True但不设置orchestrator
        personal_assistant._initialized = True
        # 故意不设置 _orchestrator

        message = "测试消息"

        with pytest.raises(RuntimeError, match="处理器协调器未初始化"):
            await personal_assistant.process_message(message, test_user, test_thread_id)

    @pytest.mark.asyncio
    async def test_process_message_orchestrator_failure(
        self,
        personal_assistant: PersonalAssistantAgent,
        test_user: str,
        test_thread_id: str,
    ) -> None:
        """测试orchestrator处理失败."""
        # 先初始化
        mock_orchestrator = AsyncMock()
        mock_orchestrator.process.side_effect = Exception(
            "Orchestrator processing failed"
        )
        personal_assistant._orchestrator = mock_orchestrator
        personal_assistant._initialized = True

        message = "测试消息"

        with pytest.raises(Exception, match="Orchestrator processing failed"):
            await personal_assistant.process_message(message, test_user, test_thread_id)

    @pytest.mark.asyncio
    @quick_test
    async def test_cleanup(self, personal_assistant: PersonalAssistantAgent) -> None:
        """测试清理资源."""
        # 先初始化
        mock_orchestrator = AsyncMock()
        mock_orchestrator.cleanup = AsyncMock()
        personal_assistant._orchestrator = mock_orchestrator
        personal_assistant._initialized = True

        await personal_assistant.cleanup()

        assert personal_assistant._initialized is False
        mock_orchestrator.cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_failure(
        self, personal_assistant: PersonalAssistantAgent
    ) -> None:
        """测试清理失败."""
        # 先初始化
        mock_orchestrator = AsyncMock()
        mock_orchestrator.cleanup = AsyncMock(side_effect=Exception("Cleanup failed"))
        personal_assistant._orchestrator = mock_orchestrator
        personal_assistant._initialized = True

        # PersonalAssistantAgent的cleanup方法会重新抛出异常
        with pytest.raises(Exception, match="Cleanup failed"):
            await personal_assistant.cleanup()

        # 验证cleanup被调用，且当清理失败时_initialized仍然为True
        # 因为异常发生在设置_initialized = False之前
        assert personal_assistant._initialized is True  # 清理失败时仍为True
        mock_orchestrator.cleanup.assert_called_once()

    @pytest.mark.asyncio
    @quick_test
    async def test_process_message_with_kwargs(
        self,
        personal_assistant: PersonalAssistantAgent,
        test_user: str,
        test_thread_id: str,
    ) -> None:
        """测试process_message方法处理kwargs参数."""
        # 先初始化
        mock_orchestrator = AsyncMock()
        # process方法应该返回元组 (result, metadata)
        mock_orchestrator.process.return_value = (
            "AI回复内容",
            {"status": "success"},
            None,
        )
        personal_assistant._orchestrator = mock_orchestrator
        personal_assistant._initialized = True

        message = "你好，请介绍一下你自己"
        kwargs = {
            "model_id": "gpt-3.5-turbo",
            "temperature": 0.8,
            "max_tokens": 2000,
            "metadata": {"source": "test"},
        }

        response = await personal_assistant.process_message(
            message, test_user, test_thread_id, **kwargs
        )

        assert response == "AI回复内容"
        mock_orchestrator.process.assert_called_once()

        # 检查传递给orchestrator的processor_config是否包含kwargs参数
        call_args = mock_orchestrator.process.call_args
        # call_args是包含位置参数和关键字参数的元组: (args, kwargs)
        args, kwargs = call_args
        processor_config = kwargs.get("processor_config") if len(args) <= 3 else args[3]

        assert processor_config is not None
        assert processor_config["model_id"] == "gpt-3.5-turbo"
        assert processor_config["temperature"] == 0.8
        assert processor_config["max_tokens"] == 2000
        assert processor_config["metadata"] == {"source": "test"}
