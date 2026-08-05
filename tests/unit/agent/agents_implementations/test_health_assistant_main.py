"""HealthAssistantAgent 单元测试.

重构后 HealthAssistantAgent 是 OrchestratorAgent 的空壳子类,
领域数据调度逻辑由基类 + DomainDataDispatcher 统一处理.
"""

from __future__ import annotations

import pytest

from src.agent.agents_implementations.base_orchestrator_agent import OrchestratorAgent
from src.agent.agents_implementations.health_assistant.main import HealthAssistantAgent
from src.config.agent_config import AgentConfig, AgentMemoryConfig


@pytest.fixture
def mock_agent_config() -> AgentConfig:
    """创建带 domain_data 的 Agent 配置."""
    return AgentConfig(
        agent_id="health-assistant",
        name="Health Assistant",
        description="健康助手",
        system_prompt="你是健康助手",
        model_id="local:qwen3.5:9b",
        llm_config={"temperature": 0.7, "max_tokens": 4000},
        tools=[],
        domain_data=["health"],
        memory=AgentMemoryConfig(),
    )


class TestHealthAssistantAgent:
    """测试重构后的 HealthAssistantAgent."""

    def test_is_orchestrator_agent_subclass(self):
        """HealthAssistantAgent 继承自 OrchestratorAgent."""
        assert issubclass(HealthAssistantAgent, OrchestratorAgent)

    def test_no_dispatch_methods(self, mock_agent_config):
        """不再有旧的调度方法 (逻辑已移至 DomainDataDispatcher + HealthDomainData)."""
        agent = HealthAssistantAgent(mock_agent_config)
        assert not hasattr(agent, "_dispatch_health_data")
        assert not hasattr(agent, "_schedule_health_data_extraction")
        assert not hasattr(agent, "_schedule_health_data_audit")
        assert not hasattr(agent, "_pending_attachment_infos")

    @pytest.mark.asyncio
    async def test_initialize_creates_dispatcher(self, mock_agent_config):
        """initialize 时根据 domain_data 配置创建 DomainDataDispatcher."""
        agent = HealthAssistantAgent(mock_agent_config)
        await agent.initialize()

        assert agent._domain_data_dispatcher is not None
        assert agent._domain_data_dispatcher.has_domain_data
        await agent.cleanup()

    @pytest.mark.asyncio
    async def test_initialize_no_dispatcher_when_domain_data_empty(self):
        """domain_data 为空时不创建 dispatcher."""
        config = AgentConfig(
            agent_id="health-assistant",
            name="Health",
            system_prompt="x",
            model_id="local:test:1",
            domain_data=[],
            memory=AgentMemoryConfig(),
        )
        agent = HealthAssistantAgent(config)
        await agent.initialize()

        assert agent._domain_data_dispatcher is None
        await agent.cleanup()
