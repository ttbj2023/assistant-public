"""Thought Assistant Agent实现.

基于OrchestratorAgent的思绪整理助手, 支持笔记整理/文稿生成/思维导图/微信推送.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config.agent_config import AgentConfig

from src.agent.agents_implementations.base_orchestrator_agent import OrchestratorAgent

logger = logging.getLogger(__name__)


class ThoughtAssistantAgent(OrchestratorAgent):
    """Thought Assistant Agent - 思绪整理助手.

    无特殊后处理逻辑, 所有通用功能由OrchestratorAgent提供.
    """

    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)


__all__ = ["ThoughtAssistantAgent"]
