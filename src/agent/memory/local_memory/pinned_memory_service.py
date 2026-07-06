"""PinnedMemoryService - 置顶记忆子系统.

从 ConversationMemoryCore 拆分, 管理置顶记忆的增量更新与周期审计.
拥有独立的模块级状态: RMW 串行化锁,审计周期跟踪,fire-and-forget 后台任务.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.storage.service import (
    create_conversation_service,
    create_todo_service,
)

if TYPE_CHECKING:
    from src.storage.models.conversation import ConversationData

logger = logging.getLogger(__name__)

# 置顶记忆周期审计: 进程级轮次跟踪(重启重置是特性, 仿 health_data_audit)
_AUDIT_INTERVAL = 20
_AUDIT_WINDOW = 20
_last_audit_round: dict[str, int] = {}


def _audit_key(user_id: str, thread_id: str, agent_id: str) -> str:
    return f"{user_id}:{thread_id}:{agent_id}"


def _should_audit(
    user_id: str,
    thread_id: str,
    agent_id: str,
    current_round: int,
) -> bool:
    last = _last_audit_round.get(_audit_key(user_id, thread_id, agent_id), 0)
    return current_round - last >= _AUDIT_INTERVAL


def _mark_audited(
    user_id: str,
    thread_id: str,
    agent_id: str,
    current_round: int,
) -> None:
    _last_audit_round[_audit_key(user_id, thread_id, agent_id)] = current_round


# 置顶记忆 RMW 串行化锁: 按 user:thread:agent 索引, 模块级跨实例共享.
# 每轮置顶更新与周期审计均为 fire-and-forget 后台任务, 共用此锁排队,
# 避免 read(LLM)write 竞态导致 lost update. 进程级累积不清理(与 _last_audit_round 对齐).
_pinned_locks: dict[str, asyncio.Lock] = {}
# 存活后台任务引用: 防 fire-and-forget task 被提前 GC(完成时自动 discard).
_pinned_bg_tasks: set[asyncio.Task[None]] = set()


def _get_pinned_lock(user_id: str, thread_id: str, agent_id: str) -> asyncio.Lock:
    """获取置顶 RMW 锁(按 user:thread:agent 索引, lazy 创建).

    单线程 asyncio 下 get+set 之间无 await, 视为原子.
    """
    key = _audit_key(user_id, thread_id, agent_id)
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
    _last_audit_round.clear()


def get_bg_tasks() -> set[asyncio.Task[None]]:
    """获取存活后台任务集合(供测试 drain 使用)."""
    return _pinned_bg_tasks


class PinnedMemoryService:
    """置顶记忆服务 - 管理增量更新与周期审计.

    每轮对话后:
    - fire-and-forget 更新: user_message + todo + memory → operations → 增量写入
    - 条件审计(每 _AUDIT_INTERVAL 轮): 读全局整理(delete/change, 无add)

    更新与审计共用 _pinned_lock 串行, 杜绝并发改同一行 lost update.
    """

    def __init__(self, user_id: str, thread_id: str, agent_id: str) -> None:
        self.user_id = user_id
        self.thread_id = thread_id
        self.agent_id = agent_id

    def on_conversation_round(self, conversation_data: ConversationData) -> None:
        """每轮对话后的置顶处理入口: fire-and-forget 更新 + 条件审计.

        由 ConversationMemoryCore.add_conversation_round 调用.
        """
        _spawn_pinned_bg_task(self.update(conversation_data))
        if _should_audit(
            self.user_id,
            self.thread_id,
            self.agent_id,
            conversation_data.round_number,
        ):
            _mark_audited(
                self.user_id,
                self.thread_id,
                self.agent_id,
                conversation_data.round_number,
            )
            _spawn_pinned_bg_task(self.audit(conversation_data))

    async def update(self, conversation_data: ConversationData) -> None:
        """置顶记忆更新 (增删改, 精确字符串匹配).

        每轮对话后执行: user_message + todo_list + current_memory → operations → 增量写入.
        """
        logger.debug(
            f"📌 开始置顶记忆更新: {conversation_data.user_id}:{conversation_data.thread_id}:{conversation_data.round_number}",
        )

        pinned_lock = _get_pinned_lock(self.user_id, self.thread_id, self.agent_id)
        await pinned_lock.acquire()
        try:
            from src.inference.content_analyzer.simple_analyzer import (
                SimpleContentAnalyzer,
            )

            from .pinned_memory import SimplePinnedMemoryManager

            analyzer = SimpleContentAnalyzer()

            pinned_manager = SimplePinnedMemoryManager(
                conversation_data.user_id,
                conversation_data.thread_id,
                agent_id=self.agent_id,
            )

            memory_block = await pinned_manager.get_memory_for_analysis()

            todo_list = await self._get_todo_list(
                conversation_data.user_id,
                conversation_data.thread_id,
            )

            result = await analyzer.analyze_pinned_memory_update(
                user_message=conversation_data.user_message,
                todo_list=todo_list,
                memory_block=memory_block,
            )

            if result.has_operations and result.operations:
                updated = await pinned_manager.apply_operations(result.operations)
                if updated:
                    logger.debug(
                        "✅ 置顶记忆已更新, 操作数: %d",
                        len(result.operations),
                    )
                else:
                    logger.debug("✅ 置顶记忆操作未产生变更")
            else:
                logger.debug("✅ 置顶记忆无需更新")

        except Exception as e:
            logger.error("❌ 置顶记忆更新失败: %s", e)
        finally:
            pinned_lock.release()

    async def audit(self, conversation_data: ConversationData) -> None:
        """周期审计: 读全局置顶 + 摘要索引, 整理(delete/change, 无add).

        基于 current 置顶状态做整理. 失败仅记日志, 不影响主流程(下个周期再来).
        mark_audited 在触发点(commit-point)已执行, 本方法无需再 mark.
        """
        round_number = conversation_data.round_number
        pinned_lock = _get_pinned_lock(self.user_id, self.thread_id, self.agent_id)
        await pinned_lock.acquire()
        try:
            from src.inference.content_analyzer.pinned_memory_audit_analyzer import (
                PinnedMemoryAuditAnalyzer,
            )

            from .pinned_memory import SimplePinnedMemoryManager

            pinned_manager = SimplePinnedMemoryManager(
                self.user_id,
                self.thread_id,
                agent_id=self.agent_id,
            )
            memory_block, number_map = await pinned_manager.get_memory_for_audit()
            if not number_map:
                return

            conv_service = await create_conversation_service(
                self.user_id,
                self.thread_id,
                agent_id=self.agent_id,
            )
            start_round = max(1, round_number - _AUDIT_WINDOW + 1)
            index_block = await conv_service.get_formatted_index_range(
                self.user_id,
                self.thread_id,
                start_round,
                round_number,
            )

            from src.config.inference_config import get_config as get_inference_config

            inference_config = get_inference_config()
            analyzer = PinnedMemoryAuditAnalyzer(
                model_id=inference_config.content_analyzer.audit_model,
                model_params=inference_config.content_analyzer.audit_model_params,
            )
            operations = await analyzer.audit(
                memory_block,
                number_map,
                index_block or "",
            )

            if operations:
                updated = await pinned_manager.apply_operations(operations)
                logger.info(
                    "📌 置顶审计 round %d: %d operations, 应用=%s",
                    round_number,
                    len(operations),
                    updated,
                )
            else:
                logger.debug("📌 置顶审计 round %d: 无需操作", round_number)
        except Exception as e:
            logger.warning(
                "📌 置顶审计 round %d 失败(不影响主流程): %s",
                round_number,
                e,
            )
        finally:
            pinned_lock.release()

    async def _get_todo_list(
        self,
        user_id: str,
        thread_id: str,
    ) -> str:
        """获取当前TODO列表字符串(待办+进行中), 供置顶记忆分析去重参考.

        Returns:
            格式化的TODO列表字符串, 失败时返回空字符串

        """
        try:
            from src.storage.models.todo import TodoStatus

            todo_service = await create_todo_service(
                user_id,
                thread_id,
                agent_id=self.agent_id,
            )
            return await todo_service.get_formatted_todolist(
                user_id,
                thread_id,
                statuses=[TodoStatus.PENDING, TodoStatus.IN_PROGRESS],
                limit=50,
                include_section_title=False,
                format_template="markdown",
            )
        except Exception as e:
            logger.debug("获取TODO列表失败, 置顶记忆分析以空TODO处理: %s", e)
            return ""
