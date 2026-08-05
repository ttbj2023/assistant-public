"""领域数据调度器 - 统一调度多个 DomainData 实例.

职责:
- 持有 agent 声明的 domain_data 名称列表, 初始化时加载实现类
- 在 post-process / post-finalize 统一 fire-and-forget 调度
- 在请求阶段收集各 domain_data 的 inject_prompt 内容
- 管理 attachment_infos 的流式跨 hook 缓存 (pre_stream → post_finalize)
- 失败隔离: 单个 domain_data 异常不影响其他
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .base_domain_data import BaseDomainData
from .domain_data_catalog import get_domain_data_class

if TYPE_CHECKING:
    from src.config.agent_config import AgentConfig
    from src.storage.models.conversation import ConversationData

logger = logging.getLogger(__name__)


class DomainDataDispatcher:
    """领域数据调度器 (agent 级别单例, 由 OrchestratorAgent 持有)."""

    def __init__(
        self,
        domain_data_names: list[str],
        agent_id: str,
    ) -> None:
        self._agent_id = agent_id
        self._pending_attachments: list[Any] | None = None
        self._bg_tasks: set[asyncio.Task[None]] = set()

        self._classes: dict[str, type[BaseDomainData]] = {}
        for name in domain_data_names:
            try:
                cls = get_domain_data_class(name)
                self._classes[name] = cls
            except (KeyError, ImportError) as e:
                logger.warning("加载领域数据 '%s' 失败, 跳过: %s", name, e)

    @property
    def has_domain_data(self) -> bool:
        """是否有已加载的 domain_data 类."""
        return len(self._classes) > 0

    def cache_attachments(self, attachments: list[Any] | None) -> None:
        """缓存流式场景的 attachment_infos (pre_stream → post_finalize 传递)."""
        self._pending_attachments = attachments

    def clear_cached_attachments(self) -> None:
        """清理缓存的 attachments."""
        self._pending_attachments = None

    async def collect_injections(
        self,
        user_id: str,
        thread_id: str,
        agent_config: AgentConfig | None = None,
    ) -> str:
        """请求阶段收集各 domain_data 的 inject_prompt 内容.

        遍历所有 domain_data 实例, 调用 inject_prompt, 合并非空结果.
        单个实例异常不影响其他.
        """
        if not self._classes:
            return ""

        parts: list[str] = []
        for name, cls in self._classes.items():
            instance = cls(user_id, thread_id, self._agent_id, agent_config)
            try:
                content = await instance.inject_prompt()
                if content:
                    parts.append(content)
            except Exception as e:
                logger.warning(
                    "领域数据 '%s' 注入失败 (%s:%s): %s",
                    name,
                    user_id,
                    thread_id,
                    e,
                )
        return "\n\n".join(parts)

    def dispatch(
        self,
        conversation_data: ConversationData | None,
        user_id: str,
        thread_id: str,
        attachment_infos: list[Any] | None,
        round_number: int | None,
        agent_config: AgentConfig | None = None,
    ) -> None:
        """统一调度所有 domain_data 的 on_conversation_round (fire-and-forget).

        非阻塞, 异常仅日志. 流式场景使用缓存的 attachments.
        """
        if not self._classes:
            return

        effective_attachments = attachment_infos or self._pending_attachments

        for name, cls in self._classes.items():
            instance = cls(user_id, thread_id, self._agent_id, agent_config)
            self._spawn(
                name,
                instance,
                conversation_data,
                effective_attachments,
                round_number,
            )

    def _spawn(
        self,
        name: str,
        instance: BaseDomainData,
        conversation_data: ConversationData | None,
        attachment_infos: list[Any] | None,
        round_number: int | None,
    ) -> None:
        """创建 fire-and-forget 后台任务."""

        async def _task() -> None:
            try:
                await instance.on_conversation_round(
                    conversation_data,
                    attachment_infos,
                    round_number,
                )
            except Exception as e:
                logger.warning(
                    "领域数据 '%s' 处理异常 (%s:%s): %s",
                    name,
                    instance.user_id,
                    instance.thread_id,
                    e,
                )

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_task())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        except RuntimeError:
            logger.warning("无法获取事件循环, 跳过领域数据 '%s' 调度", name)

    async def drain(self) -> None:
        """等待所有后台任务完成 (主要用于测试)."""
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)

    async def cleanup(self) -> None:
        """清理调度器状态."""
        self._pending_attachments = None


__all__ = ["DomainDataDispatcher"]
