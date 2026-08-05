"""经验洞察领域数据 - 从对话中提炼可复用的经验/模式/方法论.

对应 PinnedMemoryRewriter mode="simple":
- 记什么: 领域洞察、输出偏好、可复用经验(踩坑/验证过的判断/方法论)
- 每轮主模型全文覆写单一块, fire-and-forget
- 读取时注入 <pinned_memory> XML 标签到 system prompt

RMW 锁(namespace="memory")串行化覆写, 杜绝并发 lost update.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, override

from src.agent.domain_data.base_domain_data import BaseDomainData

if TYPE_CHECKING:
    from src.config.agent_config import AgentConfig
    from src.storage.models.conversation import ConversationData

logger = logging.getLogger(__name__)

_NAMESPACE = "memory"
_MODE = "simple"

_INJECTION_PREFIX = "以下是你需要长期记住的关键信息:\n"
_INJECTION_TAG = "pinned_memory"


class InsightsDomainData(BaseDomainData):
    """经验洞察领域数据 - 主模型每轮全文覆写."""

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
        agent_config: AgentConfig | None = None,
    ) -> None:
        super().__init__(user_id, thread_id, agent_id, agent_config)
        self._model_id = getattr(
            agent_config,
            "model_id",
            "deepseek:deepseek-v4-pro",
        )
        llm_config = getattr(agent_config, "llm_config", None) or {}
        self._model_params = {k: v for k, v in llm_config.items() if k != "model"}

    @override
    async def on_conversation_round(
        self,
        conversation_data: ConversationData | None,
        attachment_infos: list[Any] | None = None,  # noqa: ARG002
        round_number: int | None = None,  # noqa: ARG002
    ) -> None:
        """对话后主模型覆写经验洞察.

        从 conversation_data.metadata 取 messages 快照, 无快照则跳过.
        """
        if not conversation_data:
            return

        messages_snapshot = conversation_data.metadata.get("_messages_snapshot")
        if not messages_snapshot:
            logger.debug("无 messages 快照, 跳过经验洞察覆写")
            return

        from src.agent.memory.rmw_state import get_rmw_lock

        memory_lock = get_rmw_lock(
            _NAMESPACE,
            self.user_id,
            self.thread_id,
            self.agent_id,
        )
        await memory_lock.acquire()
        try:
            await self._do_rewrite(conversation_data, messages_snapshot)
        finally:
            memory_lock.release()

    async def _do_rewrite(
        self,
        conversation_data: ConversationData,
        messages_snapshot: list[Any],
    ) -> None:
        from src.inference.content_analyzer.pinned_memory_rewriter import (
            PinnedMemoryRewriter,
        )
        from src.storage.service import create_pinned_memory_block_service

        block_service = await create_pinned_memory_block_service(
            self.user_id,
            self.thread_id,
            agent_id=self.agent_id,
        )
        current_memory = await block_service.get_content(
            self.user_id,
            self.thread_id,
        )

        rewriter = PinnedMemoryRewriter(
            model_id=self._model_id,
            model_params=self._model_params,
        )
        result = await rewriter.rewrite(
            messages=messages_snapshot,
            response=conversation_data.assistant_response,
            current_memory=current_memory,
            mode=_MODE,
        )

        if result.needs_update and result.content:
            await block_service.set_content(
                self.user_id,
                self.thread_id,
                result.content,
            )
            from src.agent.memory.local_memory.cache import clear_pinned_memory

            clear_pinned_memory(
                self.user_id,
                self.thread_id,
                agent_id=self.agent_id,
            )
            logger.debug("经验洞察已覆写")
        else:
            logger.debug("经验洞察无需更新 (needs_update=False)")

    @override
    async def inject_prompt(self) -> str:
        """读取经验洞察, 返回注入 system prompt 的 <pinned_memory> 内容."""
        content = await self._read_with_cache()
        if not content:
            return ""
        return f"{_INJECTION_PREFIX}<{_INJECTION_TAG}>\n{content}\n</{_INJECTION_TAG}>"

    async def _read_with_cache(self) -> str:
        """缓存优先读取, DB 回退, 回填缓存."""
        try:
            from src.agent.memory.local_memory.cache import (
                get_pinned_memory,
                set_pinned_memory,
            )

            cached = get_pinned_memory(
                self.user_id,
                self.thread_id,
                agent_id=self.agent_id,
            )
            if isinstance(cached, str) and cached:
                return cached

            from src.storage.service import create_pinned_memory_block_service

            block_service = await create_pinned_memory_block_service(
                self.user_id,
                self.thread_id,
                agent_id=self.agent_id,
            )
            formatted = await block_service.get_formatted(
                self.user_id,
                self.thread_id,
            )

            if formatted:
                set_pinned_memory(
                    self.user_id,
                    self.thread_id,
                    formatted,
                    agent_id=self.agent_id,
                )
            return formatted
        except Exception as e:
            logger.error("读取经验洞察失败: %s", e)
            return ""


__all__ = ["InsightsDomainData"]
