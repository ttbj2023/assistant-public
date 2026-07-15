"""PinnedMemoryService - 置顶记忆子系统.

从 ConversationMemoryCore 拆分, 管理置顶记忆的主模型每轮覆写.
拥有独立的模块级状态: RMW 串行化锁, fire-and-forget 后台任务.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config.agent_config import AgentConfig
    from src.storage.models.conversation import ConversationData

logger = logging.getLogger(__name__)

# 置顶记忆 RMW 串行化锁: 按 user:thread:agent 索引, 模块级跨实例共享.
_pinned_locks: dict[str, asyncio.Lock] = {}
_pinned_bg_tasks: set[asyncio.Task[None]] = set()


def _lock_key(user_id: str, thread_id: str, agent_id: str) -> str:
    return f"{user_id}:{thread_id}:{agent_id}"


def _get_pinned_lock(user_id: str, thread_id: str, agent_id: str) -> asyncio.Lock:
    """获取置顶 RMW 锁(按 user:thread:agent 索引, lazy 创建)."""
    key = _lock_key(user_id, thread_id, agent_id)
    lock = _pinned_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _pinned_locks[key] = lock
    return lock


def _spawn_pinned_bg_task(coro: Any) -> None:
    """启动置顶后台任务(fire-and-forget)并登记引用防 GC."""
    task = asyncio.create_task(coro)  # type: ignore[arg-type]
    _pinned_bg_tasks.add(task)
    task.add_done_callback(_pinned_bg_tasks.discard)


def clear_module_state() -> None:
    """清理模块级状态(供测试 fixture 使用)."""
    _pinned_locks.clear()
    _pinned_bg_tasks.clear()


def get_bg_tasks() -> set[asyncio.Task[None]]:
    """获取存活后台任务集合(供测试 drain 使用)."""
    return _pinned_bg_tasks


class PinnedMemoryService:
    """置顶记忆服务 - 管理主模型每轮覆写.

    每轮对话后 fire-and-forget 覆写: messages 快照 + response → 主模型全文覆写单一块.
    通过 _pinned_lock 串行化, 杜绝并发改同一块 lost update.
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
        self.model_id = getattr(agent_config, "model_id", "deepseek:deepseek-v4-pro")
        llm_config = getattr(agent_config, "llm_config", None) or {}
        self.model_params = {k: v for k, v in llm_config.items() if k != "model"}

    def on_conversation_round(
        self,
        conversation_data: ConversationData,
        messages_snapshot: list[Any] | None = None,
    ) -> None:
        """每轮对话后的置顶处理入口: fire-and-forget 覆写."""
        _spawn_pinned_bg_task(self.update(conversation_data, messages_snapshot))

    async def update(
        self,
        conversation_data: ConversationData,
        messages_snapshot: list[Any] | None = None,
    ) -> None:
        """置顶记忆主模型覆写 (全文 overwrite).

        每轮对话后执行: messages 快照 + response + 当前记忆 → 主模型判断 →
        needs_update=True 时全文覆写单一块.
        """
        if not messages_snapshot:
            logger.debug("📌 无 messages 快照, 跳过主模型覆写")
            return

        logger.debug(
            f"📌 开始置顶记忆覆写: {conversation_data.user_id}:"
            f"{conversation_data.thread_id}:{conversation_data.round_number}",
        )

        pinned_lock = _get_pinned_lock(self.user_id, self.thread_id, self.agent_id)
        await pinned_lock.acquire()
        try:
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
                model_id=self.model_id,
                model_params=self.model_params,
            )
            result = await rewriter.rewrite(
                messages=messages_snapshot,
                response=conversation_data.assistant_response,
                current_memory=current_memory,
                mode="local",
            )

            if result.needs_update and result.content:
                await block_service.set_content(
                    self.user_id,
                    self.thread_id,
                    result.content,
                )
                from .cache import clear_pinned_memory

                clear_pinned_memory(
                    self.user_id, self.thread_id, agent_id=self.agent_id
                )
                logger.debug("✅ 置顶记忆已覆写")
            else:
                logger.debug("✅ 置顶记忆无需更新 (needs_update=False)")
        except Exception as e:
            logger.error("❌ 置顶记忆覆写失败: %s", e)
        finally:
            pinned_lock.release()
