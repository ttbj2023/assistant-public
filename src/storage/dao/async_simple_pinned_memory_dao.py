"""异步简化置顶记忆数据访问对象.

基于组合模式设计的3字段置顶记忆专用DAO,严格遵循存储层规范.
提供真正的异步数据库访问能力.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, select

from ..models.simple_pinned_memory import SimplePinnedMemory, SimplePinnedMemoryType
from .database_operations import AsyncDatabaseOperations

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class AsyncSimplePinnedMemoryDAO:
    """异步简化置顶记忆数据访问对象.

    使用组合模式,不再继承AsyncBaseDAO.
    提供置顶记忆相关的特定数据库操作.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        """初始化异步简化置顶记忆DAO.

        Args:
            session_factory: 数据库会话工厂

        """
        self.db_ops = AsyncDatabaseOperations(session_factory, SimplePinnedMemory)
        self.session_factory = session_factory
        logger.debug("AsyncSimplePinnedMemoryDAO初始化完成")

    async def get_memory_by_type(
        self,
        user_id: str,
        thread_id: str,
        memory_type: SimplePinnedMemoryType,
    ) -> SimplePinnedMemory | None:
        """异步按类型获取单条记忆记录.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            memory_type: 记忆类型

        Returns:
            记忆记录或None

        """
        try:
            filters = {
                "user_id": user_id,
                "thread_id": thread_id,
                "memory_type": memory_type,
            }
            memories = await self.db_ops.find_by_filters(filters, limit=1)
            return memories[0] if memories else None
        except Exception as e:
            logger.error("异步按类型获取置顶记忆失败: %s", e)
            raise

    async def get_all_memories(
        self,
        user_id: str,
        thread_id: str,
    ) -> list[SimplePinnedMemory]:
        """异步获取所有置顶记忆记录.

        Args:
            user_id: 用户ID
            thread_id: 线程ID

        Returns:
            所有记忆记录列表

        """
        try:
            async with self.db_ops.session_factory() as session:
                statement = (
                    select(SimplePinnedMemory)
                    .where(
                        and_(
                            SimplePinnedMemory.user_id == user_id,
                            SimplePinnedMemory.thread_id == thread_id,
                        ),
                    )
                    .order_by(SimplePinnedMemory.memory_type)
                )

                result = await session.execute(statement)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("异步获取所有置顶记忆失败: %s", e)
            raise

    async def upsert_memory(
        self,
        user_id: str,
        thread_id: str,
        memory_type: SimplePinnedMemoryType,
        content: str,
    ) -> SimplePinnedMemory:
        """异步更新或插入记忆记录.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            memory_type: 记忆类型
            content: 记忆内容

        Returns:
            更新或插入的记录

        """
        try:
            async with self.db_ops.transaction_scope() as session:
                # 尝试获取现有记录
                existing = await self.get_memory_by_type(
                    user_id,
                    thread_id,
                    memory_type,
                )

                if existing:
                    # 更新现有记录
                    stmt = (
                        select(SimplePinnedMemory)
                        .where(SimplePinnedMemory.id == existing.id)
                        .execution_options(synchronize_session="fetch")
                    )
                    result = await session.execute(stmt)
                    updated_memory = result.scalar_one()

                    # 更新字段
                    updated_memory.content = content
                    updated_memory.updated_at = datetime.now(UTC)

                    await session.flush()
                    await session.refresh(updated_memory)
                    return updated_memory

                # 创建新记录
                return await self.db_ops.create_with_validation(
                    required_fields=["user_id", "thread_id", "memory_type"],
                    user_id=user_id,
                    thread_id=thread_id,
                    memory_type=memory_type,
                    content=content,
                )
        except Exception as e:
            logger.error("异步更新或插入置顶记忆失败: %s", e)
            raise

    async def delete_memory(
        self,
        user_id: str,
        thread_id: str,
        memory_type: SimplePinnedMemoryType,
    ) -> bool:
        """异步删除指定类型的记忆记录.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            memory_type: 记忆类型

        Returns:
            是否删除成功

        """
        try:
            async with self.db_ops.transaction_scope() as session:
                statement = delete(SimplePinnedMemory).where(
                    and_(
                        SimplePinnedMemory.user_id == user_id,
                        SimplePinnedMemory.thread_id == thread_id,
                        SimplePinnedMemory.memory_type == memory_type,
                    ),
                )
                result = await session.execute(statement)
                return result.rowcount > 0
        except Exception as e:
            logger.error("异步删除置顶记忆失败: %s", e)
            raise

    async def bulk_create(
        self,
        items: list[dict[str, Any]],
    ) -> list[SimplePinnedMemory]:
        """异步批量创建记忆记录.

        Args:
            items: 记录字典列表

        Returns:
            创建的记录列表

        """
        return await self.db_ops.bulk_create(
            items,
            required_fields=["user_id", "thread_id", "memory_type"],
        )

    async def health_check(self) -> bool:
        """异步健康检查.

        Returns:
            是否健康

        """
        return await self.db_ops.health_check()


__all__ = [
    "AsyncSimplePinnedMemoryDAO",
]
