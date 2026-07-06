"""用户级文件注册表数据访问对象.

提供文件元数据 CRUD + 去重查询 + 引用计数实时统计 + 配额统计.

设计要点:
- 引用计数无字段维护, 通过 count_by_content_hash 实时查询 (消除并发递增竞态)
- 配额总大小按 physical_path 去重求和 (同内容文件共享物理副本, 不重复计算)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, desc, func, select

from src.storage.models.file_registry import FileEntry

from .database_operations import AsyncDatabaseOperations

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class AsyncFileRegistryDAO:
    """用户级文件注册表 DAO."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.db_ops = AsyncDatabaseOperations(session_factory, FileEntry)
        self.session_factory = session_factory

    async def get_by_file_id(self, file_id: str) -> FileEntry | None:
        """按 file_id 精确查询 (主键)."""
        try:
            results = await self.db_ops.find_by_filters({"file_id": file_id}, limit=1)
            return results[0] if results else None
        except Exception as e:
            logger.error("查询文件注册表失败: %s", e)
            raise

    async def find_by_content_hash(
        self,
        content_hash: str,
    ) -> FileEntry | None:
        """按 content_hash 查询 (LIMIT 1).

        用于两个场景:
        - 去重: 命中则复用 physical_path, 不写新物理文件
        - 历史反查: 按 base64 图片哈希反查文件元数据

        Args:
            content_hash: 文件内容 SHA-256 哈希 (64位hex)

        Returns:
            匹配的记录 (任意一条), 无匹配返回 None

        """
        try:
            results = await self.db_ops.find_by_filters(
                {"content_hash": content_hash},
                limit=1,
            )
            return results[0] if results else None
        except Exception as e:
            logger.error("按 content_hash 查询文件注册表失败: %s", e)
            raise

    async def list_all(self) -> list[FileEntry]:
        """列出所有文件记录."""
        try:
            return await self.db_ops.find_by_filters({})
        except Exception as e:
            logger.error("列出文件注册表失败: %s", e)
            raise

    async def list_recent_by_type(
        self,
        file_type: str,
        limit: int = 10,
    ) -> list[FileEntry]:
        """按类型列出最近文件 (轮次降序 + 创建时间降序)."""
        try:
            async with self.session_factory() as session:
                stmt = (
                    select(FileEntry)
                    .where(FileEntry.file_type == file_type)
                    .order_by(
                        desc(FileEntry.round_number),
                        desc(FileEntry.created_at),
                    )
                    .limit(limit)
                )
                result = await session.execute(stmt)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("按类型列出最近文件失败: %s", e)
            raise

    async def upsert(self, data: dict[str, Any]) -> FileEntry:
        """插入或更新文件记录 (按 file_id 幂等)."""
        try:
            async with self.db_ops.transaction_scope() as session:
                stmt = select(FileEntry).where(
                    FileEntry.file_id == data["file_id"],
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    for k, v in data.items():
                        setattr(existing, k, v)
                    await session.flush()
                    await session.refresh(existing)
                    return existing

                entry = FileEntry(**data)
                session.add(entry)
                await session.flush()
                await session.refresh(entry)
                return entry
        except Exception as e:
            logger.error("写入文件注册表失败: %s", e)
            raise

    async def delete_by_file_id(self, file_id: str) -> bool:
        """按 file_id 删除文件记录.

        Returns:
            是否删除了记录 (False 表示记录不存在)

        """
        try:
            async with self.session_factory() as session:
                stmt = delete(FileEntry).where(FileEntry.file_id == file_id)
                result = await session.execute(stmt)
                await session.commit()
                deleted = result.rowcount > 0
                if deleted:
                    logger.info("🗑️ 删除文件注册表记录: %s", file_id)
                return deleted
        except Exception as e:
            logger.error("删除文件注册表记录失败: %s", e)
            raise

    async def count_by_content_hash(self, content_hash: str) -> int:
        """实时统计同 content_hash 的引用数.

        替代旧 reference_count 字段维护. 用于清理时判断物理文件是否可删:
        删一条记录后, 若同 content_hash 记录数为 0, 则物理文件可删.

        Args:
            content_hash: 文件内容 SHA-256 哈希

        Returns:
            同哈希的记录数 (含当前正在判断的记录, 调用方应在删除后查询)

        """
        try:
            async with self.session_factory() as session:
                stmt = (
                    select(func.count())
                    .select_from(FileEntry)
                    .where(
                        FileEntry.content_hash == content_hash,
                    )
                )
                result = await session.execute(stmt)
                return result.scalar_one()
        except Exception as e:
            logger.error("统计 content_hash 引用数失败: %s", e)
            raise

    async def list_ordered_by_created(self) -> list[FileEntry]:
        """按创建时间升序列出全部记录 (最早的在前).

        用于配额清理: 超限时优先清理最早创建的文件.

        """
        try:
            async with self.session_factory() as session:
                stmt = select(FileEntry).order_by(FileEntry.created_at.asc())
                result = await session.execute(stmt)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("按创建时间列出文件失败: %s", e)
            raise

    async def get_total_unique_size(self) -> int:
        """计算去重后的物理文件总大小 (字节).

        按 physical_path 分组求和: 同内容文件共享物理副本, 不重复计算.
        用于配额统计.

        Returns:
            去重后的文件总大小 (字节)

        """
        try:
            async with self.session_factory() as session:
                # 按 physical_path 分组取每组 file_size, 再求和
                subq = (
                    select(
                        FileEntry.physical_path,
                        func.max(FileEntry.file_size).label("file_size"),
                    )
                    .group_by(FileEntry.physical_path)
                    .subquery()
                )
                stmt = select(func.sum(subq.c.file_size))
                result = await session.execute(stmt)
                total = result.scalar_one_or_none()
                return total or 0
        except Exception as e:
            logger.error("计算去重后文件总大小失败: %s", e)
            raise

    async def health_check(self) -> bool:
        """健康检查."""
        return await self.db_ops.health_check()
