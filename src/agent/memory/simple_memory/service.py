"""SimpleMemoryService - Simple 模式长期记忆子系统.

管理长期记忆(preferences/insights)的增量更新. 每轮对话后 fire-and-forget
执行 Stage 1 提取: 单次完整交换(用户消息 + 助手回复) + 已有记忆 -> operations
-> 增量写入(三层去重).

拥有独立的模块级状态: RMW 串行化锁,fire-and-forget 后台任务.
周期审计与 Stage 2 综合(洞察提炼)属 P2, 此处暂不实现.

与 PinnedMemoryService 的关系: 模式同构, 但提取输入含 assistant_response,
字段为 preferences/insights, 无 TODO 去重参考.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.storage.models.conversation import ConversationData

logger = logging.getLogger(__name__)

# RMW 串行化锁: 按 user:thread:agent 索引, 模块级跨实例共享.
# fire-and-forget 后台任务共用此锁排队, 避免 read(LLM)write 竞态导致 lost update.
_memory_locks: dict[str, asyncio.Lock] = {}
# 存活后台任务引用: 防 fire-and-forget task 被提前 GC(完成时自动 discard).
_memory_bg_tasks: set[asyncio.Task[None]] = set()


def _lock_key(user_id: str, thread_id: str, agent_id: str) -> str:
    return f"{user_id}:{thread_id}:{agent_id}"


def _get_memory_lock(user_id: str, thread_id: str, agent_id: str) -> asyncio.Lock:
    """获取 RMW 锁(按 user:thread:agent 索引, lazy 创建).

    单线程 asyncio 下 get+set 之间无 await, 视为原子.
    """
    key = _lock_key(user_id, thread_id, agent_id)
    lock = _memory_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _memory_locks[key] = lock
    return lock


def _spawn_bg_task(coro: Any) -> None:
    """启动后台任务(fire-and-forget)并登记引用防 GC."""
    task = asyncio.create_task(coro)  # type: ignore[arg-type]
    _memory_bg_tasks.add(task)
    task.add_done_callback(_memory_bg_tasks.discard)


def clear_module_state() -> None:
    """清理模块级状态(供测试 fixture 使用)."""
    _memory_locks.clear()
    _memory_bg_tasks.clear()


def get_bg_tasks() -> set[asyncio.Task[None]]:
    """获取存活后台任务集合(供测试 drain 使用)."""
    return _memory_bg_tasks


class SimpleMemoryService:
    """Simple 模式长期记忆服务 - 管理 Stage 1 增量提取.

    每轮对话后 fire-and-forget: 单次完整交换 + 已有记忆 -> operations -> 增量写入.
    """

    def __init__(self, user_id: str, thread_id: str, agent_id: str) -> None:
        self.user_id = user_id
        self.thread_id = thread_id
        self.agent_id = agent_id

    def on_conversation_round(self, conversation_data: ConversationData) -> None:
        """每轮对话后的提取入口: fire-and-forget Stage 1 更新."""
        _spawn_bg_task(self.update(conversation_data))

    async def update(self, conversation_data: ConversationData) -> None:
        """长期记忆 Stage 1 提取(增删改, 精确字符串匹配 + 语义去重).

        每轮对话后执行: user_message + assistant_response + current_memory
        -> operations -> 增量写入.
        """
        logger.debug(
            f"📌 开始长期记忆更新: {conversation_data.user_id}:{conversation_data.thread_id}:{conversation_data.round_number}",
        )

        memory_lock = _get_memory_lock(self.user_id, self.thread_id, self.agent_id)
        await memory_lock.acquire()
        try:
            from src.inference.content_analyzer.simple_memory_analyzer import (
                SimpleMemoryAnalyzer,
            )

            from .manager import SimpleMemoryManager

            analyzer = SimpleMemoryAnalyzer()
            manager = SimpleMemoryManager(
                conversation_data.user_id,
                conversation_data.thread_id,
                agent_id=self.agent_id,
            )

            memory_block = await manager.get_memory_for_analysis()

            result = await analyzer.analyze_memory_update(
                user_message=conversation_data.user_message,
                assistant_response=conversation_data.assistant_response,
                memory_block=memory_block,
            )

            if result.has_operations and result.operations:
                updated = await manager.apply_operations(result.operations)
                if updated:
                    logger.debug(
                        "✅ 长期记忆已更新, 操作数: %d",
                        len(result.operations),
                    )
                else:
                    logger.debug("✅ 长期记忆操作未产生变更")
            else:
                logger.debug("✅ 长期记忆无需更新")

        except Exception as e:
            logger.error("❌ 长期记忆更新失败: %s", e)
        finally:
            memory_lock.release()


__all__ = ["SimpleMemoryService"]
