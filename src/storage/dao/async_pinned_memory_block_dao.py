"""异步统一置顶记忆单一块数据访问对象.

组合 AsyncDatabaseOperations, 提供 get/upsert/delete (每 user/thread 单行).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, delete, select

from ..models.pinned_memory_block import PinnedMemoryBlock
from .database_operations import AsyncDatabaseOperations

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class AsyncPinnedMemoryBlockDAO:
    """异步统一置顶记忆单一块 DAO (每 user/thread 单行)."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.db_ops = AsyncDatabaseOperations(session_factory, PinnedMemoryBlock)
        self.session_factory = session_factory
        logger.debug("AsyncPinnedMemoryBlockDAO初始化完成")

    async def get(self, user_id: str, thread_id: str) -> PinnedMemoryBlock | None:
        """获取单条记忆块记录."""
        try:
            async with self.session_factory() as session:
                statement = select(PinnedMemoryBlock).where(
                    and_(
                        PinnedMemoryBlock.user_id == user_id,
                        PinnedMemoryBlock.thread_id == thread_id,
                    ),
                )
                result = await session.execute(statement)
                return result.scalar_one_or_none()
        except Exception as e:
            logger.error("获取统一置顶记忆块失败: %s", e)
            raise

    async def upsert(
        self,
        user_id: str,
        thread_id: str,
        content: str,
    ) -> PinnedMemoryBlock:
        """更新或插入记忆块 (全文覆盖)."""
        try:
            async with self.db_ops.transaction_scope() as session:
                statement = (
                    select(PinnedMemoryBlock)
                    .where(
                        and_(
                            PinnedMemoryBlock.user_id == user_id,
                            PinnedMemoryBlock.thread_id == thread_id,
                        ),
                    )
                    .execution_options(synchronize_session="fetch")
                )
                result = await session.execute(statement)
                existing = result.scalar_one_or_none()

                if existing:
                    existing.content = content
                    existing.updated_at = datetime.now(UTC)
                    await session.flush()
                    await session.refresh(existing)
                    return existing

                return await self.db_ops.create_with_validation(
                    required_fields=["user_id", "thread_id"],
                    user_id=user_id,
                    thread_id=thread_id,
                    content=content,
                )
        except Exception as e:
            logger.error("更新或插入统一置顶记忆块失败: %s", e)
            raise

    async def delete(self, user_id: str, thread_id: str) -> bool:
        """清空记忆块."""
        try:
            async with self.db_ops.transaction_scope() as session:
                statement = delete(PinnedMemoryBlock).where(
                    and_(
                        PinnedMemoryBlock.user_id == user_id,
                        PinnedMemoryBlock.thread_id == thread_id,
                    ),
                )
                result = await session.execute(statement)
                return result.rowcount > 0
        except Exception as e:
            logger.error("清空统一置顶记忆块失败: %s", e)
            raise

    async def health_check(self) -> bool:
        return await self.db_ops.health_check()


__all__ = ["AsyncPinnedMemoryBlockDAO"]
