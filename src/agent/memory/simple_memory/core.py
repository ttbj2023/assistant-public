"""SimpleMemoryCore - Simple 模式对话记忆核心.

对话完成后统一触发点, 仅执行两件事:
1. 存储当前轮(用户消息 + 助手回复, 复用 conversation_index 表, 跳过索引/向量/弧短语)
2. fire-and-forget 触发 Stage 1 长期记忆提取

与 ConversationMemoryCore 的区别:
- 不做向量存储 / 索引生成 / 缓存更新 / run 检测
- 轮次存储复用 ConversationService(原始 round 落库, topic/summary 留空)
- prompt 历史由前端透传, 不从此处读取
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

from src.storage.service import create_conversation_service

from .service import SimpleMemoryService

if TYPE_CHECKING:
    from src.config.agent_config import AgentConfig
    from src.storage.models.conversation import ConversationData

logger = logging.getLogger(__name__)


class SimpleMemoryCore:
    """Simple 模式对话记忆核心.

    职责: 存当前轮 + 触发长期记忆 Stage 1 提取. 不管理 prompt 历史(前端透传).
    """

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        agent_config: AgentConfig | None = None,
    ) -> None:
        self.user_id = user_id
        self.thread_id = thread_id
        self.agent_config = agent_config
        if not agent_config or not agent_config.agent_id:
            raise ValueError(
                "SimpleMemoryCore 初始化失败: agent_id 不能为空, "
                "agent_config 必须包含 agent_id 属性",
            )
        self.agent_id: str = agent_config.agent_id

        self._memory_svc = SimpleMemoryService(
            self.user_id,
            self.thread_id,
            self.agent_id,
            agent_config=agent_config,
        )

    async def add_conversation_round(self, conversation_data: ConversationData) -> None:
        """对话完成后的统一触发点: 存当前轮 + 触发 Stage 1 提取.

        Args:
            conversation_data: 统一对话数据(含预分配 round_number)

        """
        logger.debug(
            f"💚 SimpleMemoryCore.add_conversation_round: {self.user_id}:{self.thread_id}:{conversation_data.round_number}",
        )

        try:
            # 1. 存当前轮(复用 conversation_index 表, 仅原始内容, 跳过索引/向量)
            await self._store_round(conversation_data)

            # 2. fire-and-forget 触发主模型覆写
            messages_snapshot = conversation_data.metadata.get("_messages_snapshot")
            self._memory_svc.on_conversation_round(
                conversation_data,
                messages_snapshot=messages_snapshot,
            )

            logger.debug(
                f"✅ SimpleMemoryCore 完成: {self.user_id}:{self.thread_id}:{conversation_data.round_number}",
            )
        except Exception as e:
            logger.error(f"❌ SimpleMemoryCore 处理失败: {type(e).__name__}: {e}")
            error_msg = str(e).lower()
            if any(
                kw in error_msg
                for kw in [
                    "database",
                    "constraint",
                    "sql",
                    "sqlite",
                    "connection",
                    "transaction",
                    "persist",
                    "storage",
                ]
            ):
                raise RuntimeError(f"数据持久化失败: {e}") from e
            logger.warning("⚠️ 非关键错误, 继续主流程: %s", e)

    async def _store_round(self, conversation_data: ConversationData) -> None:
        """存储当前轮到 conversation_index 表(原始内容, topic/summary 留空).

        复用 ConversationService.create_conversation, 传入预分配的 round_number.
        """
        conv_service = await create_conversation_service(
            conversation_data.user_id,
            conversation_data.thread_id,
            agent_id=self.agent_id,
        )
        await conv_service.create_conversation(
            user_message=conversation_data.user_message,
            assistant_response=conversation_data.assistant_response,
            user_id=conversation_data.user_id,
            thread_id=conversation_data.thread_id,
            agent_id=conversation_data.agent_id,
            metadata=conversation_data.metadata,
            round_number=conversation_data.round_number,
        )
        logger.debug(
            f"💾 当前轮已存储: round={conversation_data.round_number}",
        )

    @override
    def __str__(self) -> str:
        return (
            f"SimpleMemoryCore(user_id={self.user_id}, "
            f"thread_id={self.thread_id}, agent_id={self.agent_id})"
        )

    @override
    def __repr__(self) -> str:
        return (
            f"SimpleMemoryCore(user_id='{self.user_id}', "
            f"thread_id='{self.thread_id}', agent_id='{self.agent_id}')"
        )


__all__ = ["SimpleMemoryCore"]
