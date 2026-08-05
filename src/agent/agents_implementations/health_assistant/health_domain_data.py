"""健康领域数据 - 从对话中提取健康数据.

封装 HealthDataBackgroundExtractor (常规提取) 和 run_audit (周期审计),
内部根据轮次自决走哪条路径, 调度层不感知审计的存在.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, override

from src.agent.domain_data.base_domain_data import BaseDomainData

from .health_data_audit import run_audit, should_audit
from .health_data_background_extractor import HealthDataBackgroundExtractor

if TYPE_CHECKING:
    from src.config.agent_config import AgentConfig
    from src.storage.models.conversation import ConversationData

logger = logging.getLogger(__name__)


class HealthDomainData(BaseDomainData):
    """健康领域数据 - 对话后自动提取健康数据."""

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
        agent_config: AgentConfig | None = None,
    ) -> None:
        super().__init__(user_id, thread_id, agent_id, agent_config)
        self._extractor = HealthDataBackgroundExtractor(
            user_id,
            thread_id,
            agent_id=agent_id,
        )

    @override
    async def on_conversation_round(
        self,
        conversation_data: ConversationData | None,
        attachment_infos: list[Any] | None = None,
        round_number: int | None = None,
    ) -> None:
        """对话后健康数据处理: 审计轮走 audit, 否则走常规提取.

        无对话数据时直接跳过 (与原 HealthAssistantAgent._dispatch_health_data 一致).
        """
        if not conversation_data:
            return

        effective_round = round_number or 0

        if effective_round > 0 and should_audit(
            self.user_id,
            self.thread_id,
            self.agent_id,
            effective_round,
        ):
            await run_audit(
                self.user_id,
                self.thread_id,
                self.agent_id,
                effective_round,
                user_message=conversation_data.user_message,
                attachment_infos=attachment_infos,
            )
        else:
            await self._extractor.extract_from_conversation(
                user_message=conversation_data.user_message,
                attachment_infos=attachment_infos,
                round_number=round_number,
            )


__all__ = ["HealthDomainData"]
