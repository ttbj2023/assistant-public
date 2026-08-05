"""领域数据基类 - 从对话中提取结构化领域数据的统一契约.

与 memory(对话上下文) 正交: memory 管对话历史, domain_data 管结构化领域数据.
两者都挂在对话生命周期的 post-process/finalize 点, 但走独立路径, 互不感知.

能力契约 (至少具备 on_conversation_round):
- on_conversation_round: 后台提取入口 (必须)
- inject_prompt: 注入 system prompt 的内容 (可选, Phase 2 pinned_memory 用)
- cleanup: 资源清理 (可选)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config.agent_config import AgentConfig
    from src.storage.models.conversation import ConversationData

logger = logging.getLogger(__name__)


class BaseDomainData(ABC):
    """领域数据基类.

    子类实现 on_conversation_round 完成具体的提取/审计逻辑.
    调度层 (DomainDataDispatcher) 负责 fire-and-forget, 实例不需要管理任务生命周期.
    """

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
        agent_config: AgentConfig | None = None,
    ) -> None:
        self.user_id = user_id
        self.thread_id = thread_id
        self.agent_id = agent_id
        self.agent_config = agent_config

    @abstractmethod
    async def on_conversation_round(
        self,
        conversation_data: ConversationData | None,
        attachment_infos: list[Any] | None = None,
        round_number: int | None = None,
    ) -> None:
        """对话结束后的领域数据处理入口.

        实现内部自行决定策略 (常规 extract / 周期 audit), 调度层不感知.

        Args:
            conversation_data: 对话数据, None 表示无对话数据时应跳过
            attachment_infos: 附件信息列表 (含AI生成的图片描述)
            round_number: 对话轮次号

        """

    async def inject_prompt(self) -> str:
        """返回注入 system prompt 的内容, 默认空."""
        return ""

    async def cleanup(self) -> None:
        """清理资源, 默认空."""


__all__ = ["BaseDomainData"]
