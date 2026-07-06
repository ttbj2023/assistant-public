"""用户级文件注册表业务服务.

单一 SSOT, 整合旧 AttachmentRegistryService + FileDeduplicationService 职责.
提供文件元数据 CRUD + 去重查询 + 引用计数实时统计 + 配额统计.

设计约定:
- 用户级单例: 每个用户一个 FileRegistryService 实例
- 去重范围: 同一用户跨线程跨 Agent
- 引用计数: 无字段维护, 实时 count_by_content_hash 查询
- 清理权威: 以本表为驱动, 单库事务保证原子性
"""

from __future__ import annotations

import logging
from typing import Any

from src.storage.dao.async_file_registry_dao import AsyncFileRegistryDAO
from src.storage.models.file_registry import FileEntry

logger = logging.getLogger(__name__)


class FileRegistryService:
    """用户级文件注册表服务."""

    def __init__(self, session_factory: Any, user_id: str) -> None:
        self.session_factory = session_factory
        self.user_id = user_id
        self.dao = AsyncFileRegistryDAO(session_factory)
        self.logger = logging.getLogger(f"{__name__}.FileRegistryService")

    async def get(self, file_id: str) -> FileEntry | None:
        """按 file_id 查询."""
        return await self.dao.get_by_file_id(file_id)

    async def find_by_content_hash(
        self,
        content_hash: str,
    ) -> FileEntry | None:
        """按 content_hash 查询 (去重命中判断 / 历史反查)."""
        return await self.dao.find_by_content_hash(content_hash)

    async def list_all(self) -> list[FileEntry]:
        """列出所有文件记录."""
        return await self.dao.list_all()

    async def list_recent_images(self, limit: int = 10) -> list[FileEntry]:
        """列出最近的图片文件."""
        return await self.dao.list_recent_by_type("image", limit)

    async def list_recent_documents(self, limit: int = 10) -> list[FileEntry]:
        """列出最近的文档文件."""
        return await self.dao.list_recent_by_type("document", limit)

    async def upsert(self, entry: FileEntry) -> None:
        """插入或更新文件记录 (按 file_id 幂等)."""
        await self.dao.upsert(entry.model_dump())

    async def delete(self, file_id: str) -> bool:
        """删除文件记录 (仅删 DB 记录, 物理文件清理由上层编排)."""
        return await self.dao.delete_by_file_id(file_id)

    async def count_references(self, content_hash: str) -> int:
        """实时统计同 content_hash 的引用数 (替代 reference_count 字段).

        清理决策依据: 删除一条记录后, 若同 content_hash 引用数为 0,
        则物理文件可安全删除.

        """
        return await self.dao.count_by_content_hash(content_hash)

    async def list_ordered_by_created(self) -> list[FileEntry]:
        """按创建时间升序列出全部记录 (配额清理用)."""
        return await self.dao.list_ordered_by_created()

    async def get_total_unique_size(self) -> int:
        """计算去重后的物理文件总大小 (配额统计用)."""
        return await self.dao.get_total_unique_size()


async def create_file_registry_service(user_id: str) -> FileRegistryService:
    """创建用户级文件注册表服务实例 (用户级, 全局缓存复用 Engine).

    数据库存储在 data/{user_id}/database/file_registry.db, 与旧 file_store.db
    并存 (Phase 1 数据层就绪, 不切换读写).

    Args:
        user_id: 用户ID

    Returns:
        文件注册表服务实例

    """
    from src.storage.dao.async_database_manager import (
        create_async_file_registry_db_manager,
    )

    db_manager = await create_async_file_registry_db_manager(user_id)
    return FileRegistryService(db_manager.session_factory, user_id)
