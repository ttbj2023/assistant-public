"""Health Assistant Agent具体实现.

基于OrchestratorAgent中间基类的健康管理Agent.
领域数据(健康数据提取+审计)通过 agent.yaml domain_data 配置启用,
调度逻辑由基类 OrchestratorAgent 统一处理.
"""

from __future__ import annotations

from src.agent.agents_implementations.base_orchestrator_agent import OrchestratorAgent


class HealthAssistantAgent(OrchestratorAgent):
    """Health Assistant Agent - 健康管理Agent.

    领域数据通过 agent.yaml domain_data: ["health"] 声明启用,
    提取/审计的 fire-and-forget 调度由 OrchestratorAgent 基类统一处理.
    """


__all__ = ["HealthAssistantAgent"]
