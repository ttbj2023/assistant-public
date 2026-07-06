"""Personal Assistant Agent具体实现.

基于OrchestratorAgent中间基类的通用个人助手, 无特殊后处理逻辑.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config.agent_config import AgentConfig

from src.agent.agents_implementations.base_orchestrator_agent import OrchestratorAgent

logger = logging.getLogger(__name__)


class PersonalAssistantAgent(OrchestratorAgent):
    """Personal Assistant Agent - 通用个人助手.

    无特殊后处理逻辑, 所有通用功能由OrchestratorAgent提供.
    """

    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)


__all__ = ["PersonalAssistantAgent"]
