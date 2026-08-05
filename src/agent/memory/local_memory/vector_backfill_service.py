"""VectorBackfillService - 向量库缺失轮次的懒补偿.

每轮对话后(fire-and-forget): 从 SQL 对话索引 diff 出向量库缺失的轮次, 逐轮
重新嵌入补入. 兜住 Ollama 等嵌入服务持久故障导致的向量数据丢失(重试也救不回
的那部分), 原理同 scripts/rebuild_vector_store.py, 但增量、懒触发、无需运维介入.

doc_id 与 SQL (user_id, thread_id, agent_id, round_number) 唯一键对齐
(见 langchain_vector_store.add_conversation_round), 故补偿天然幂等: 重复补入
同一轮不会产生重复向量记录(upsert).

内存 frontier 游标: 记录已确认连续完整的最大 round, 下次 SQL 扫描从 frontier+1
起, 避免重复全扫. 进程重启丢失(重置为 0), 重新全量 diff 一次, 成本可接受.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from src.storage.langchain_vector_store import create_langchain_vector_store
from src.storage.service import create_conversation_service

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings

    from src.storage.models.conversation import ConversationData

logger = logging.getLogger(__name__)

# 已确认连续完整的最大 round: key(user:thread:agent) -> round_number
_frontiers: dict[str, int] = {}
# RMW 串行化锁: 按 user:thread:agent 索引, 防并发重复补偿
_backfill_locks: dict[str, asyncio.Lock] = {}
# 存活后台任务引用: 防 fire-and-forget task 被提前 GC
_backfill_bg_tasks: set[asyncio.Task[None]] = set()


def _backfill_key(user_id: str, thread_id: str, agent_id: str) -> str:
    return f"{user_id}:{thread_id}:{agent_id}"


def _get_backfill_lock(user_id: str, thread_id: str, agent_id: str) -> asyncio.Lock:
    """获取补偿 RMW 锁(按 user:thread:agent 索引, lazy 创建)."""
    key = _backfill_key(user_id, thread_id, agent_id)
    lock = _backfill_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _backfill_locks[key] = lock
    return lock


def _spawn_backfill_bg_task(coro: object) -> None:
    """启动补偿后台任务(fire-and-forget)并登记引用防 GC."""
    task: asyncio.Task[None] = asyncio.create_task(coro)  # type: ignore[arg-type]
    _backfill_bg_tasks.add(task)
    task.add_done_callback(_backfill_bg_tasks.discard)


def _embeddings_enabled() -> bool:
    """embedding 关闭时跳过补偿(无向量存储, 补了也无用)."""
    try:
        from src.config.inference_config import get_config

        return bool(get_config().embeddings.enabled)
    except Exception as e:
        logger.debug("读取 embedding 配置失败, 默认启用: %s", e)
        return True


def clear_module_state() -> None:
    """清理模块级状态(供测试 fixture 使用)."""
    _backfill_locks.clear()
    _backfill_bg_tasks.clear()
    _frontiers.clear()


class VectorBackfillService:
    """向量库缺失轮次的懒补偿服务(fire-and-forget).

    由 ConversationMemoryCore 在并行存储完成后调用, 与 IndexRunService 并列.
    """

    def __init__(self, user_id: str, thread_id: str, agent_id: str) -> None:
        self.user_id = user_id
        self.thread_id = thread_id
        self.agent_id = agent_id
        self._embeddings: Embeddings | None = None

    def on_conversation_round(self, conversation_data: ConversationData) -> None:
        """每轮对话后的向量补偿入口(fire-and-forget).

        由 ConversationMemoryCore.add_conversation_round 在存储完成后调用.
        """
        _spawn_backfill_bg_task(self._backfill_round(conversation_data))

    def _get_embeddings(self) -> Embeddings | None:
        """惰性创建 embedding 客户端."""
        if self._embeddings is None:
            try:
                from src.inference.embeddings.embeddings import create_embeddings

                self._embeddings = create_embeddings()
            except Exception as e:
                logger.warning("创建 embedding 客户端失败, 跳过补偿: %s", e)
                return None
        return self._embeddings

    async def _backfill_round(self, conversation_data: ConversationData) -> None:
        """核心: diff SQL 与向量库, 逐轮补入缺失轮次(独立容错)."""
        if not _embeddings_enabled():
            return
        embeddings = self._get_embeddings()
        if embeddings is None:
            return

        lock = _get_backfill_lock(self.user_id, self.thread_id, self.agent_id)
        await lock.acquire()
        try:
            await self._compensate_missing(conversation_data)
        except Exception as e:
            logger.warning(
                "向量补偿失败(不影响主流程): %s",
                e,
            )
        finally:
            lock.release()

    async def _compensate_missing(self, conversation_data: ConversationData) -> None:
        """SQL diff 向量库, 补入缺失轮次并推进 frontier."""
        cur_round = conversation_data.round_number
        key = _backfill_key(self.user_id, self.thread_id, self.agent_id)
        frontier = _frontiers.get(key, 0)

        # SQL 查 [frontier+1, cur_round] 范围轮次(增量扫描)
        conv_service = await create_conversation_service(
            self.user_id,
            self.thread_id,
            agent_id=self.agent_id,
        )
        rounds = await conv_service.get_conversations_in_range(
            frontier + 1,
            cur_round,
            self.user_id,
            self.thread_id,
        )
        if not rounds:
            return

        # 向量库查已有 round 集合
        vector_store = create_langchain_vector_store(
            self.user_id,
            self.thread_id,
            self.agent_id,
        )
        existing = await vector_store.get_existing_round_numbers()

        # diff 缺失轮次
        missing = [r for r in rounds if r.round_number not in existing]
        if missing:
            logger.debug(
                "向量补偿: 检测到 %d 个缺失轮次 %s",
                len(missing),
                sorted(r.round_number for r in missing),
            )

        # 逐轮补入(独立容错: 单轮失败不阻断后续)
        backfilled: set[int] = set()
        for r in missing:
            try:
                await vector_store.add_conversation_round(
                    round_number=r.round_number,
                    user_message=r.user_message,
                    assistant_response=r.assistant_response,
                    agent_id=self.agent_id,
                )
                backfilled.add(r.round_number)
            except Exception as e:
                logger.warning(
                    "向量补偿 round %d 失败, 跳过: %s",
                    r.round_number,
                    e,
                )

        # frontier 推进到连续完整的最大 round(遇缺口停止)
        combined = existing | backfilled
        new_frontier = frontier
        for rn in range(frontier + 1, cur_round + 1):
            if rn in combined:
                new_frontier = rn
            else:
                break
        _frontiers[key] = new_frontier

        if backfilled:
            logger.info(
                "✅ 向量补偿完成: 补入 %d 轮, frontier %d→%d",
                len(backfilled),
                frontier,
                new_frontier,
            )
