"""Graph 同步映射表 DAO.

upsert 语义: (user_id, kind, local_id) 唯一, 存在即更新 remote_id/hash/时间.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.storage.models.sync_map import SyncItemKind, SyncMap

from .database_operations import AsyncDatabaseOperations

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class AsyncSyncMapDAO:
    """用户级 Graph 同步映射 DAO."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.db_ops = AsyncDatabaseOperations(session_factory, SyncMap)
        self.session_factory = session_factory

    async def upsert(
        self,
        *,
        user_id: str,
        kind: SyncItemKind,
        local_id: int,
        remote_id: str,
        content_hash: str | None = None,
        last_synced_at: datetime | None = None,
    ) -> SyncMap:
        """写入或更新映射 (按 user_id+kind+local_id 定位).

        Args:
            user_id: 用户ID
            kind: 对象类型
            local_id: 本地条目ID
            remote_id: Graph 对象ID
            content_hash: 上次同步成功时的内容摘要
            last_synced_at: 上次成功同步时间

        Returns:
            持久化后的映射记录

        """
        existing = await self._find_one(user_id, kind, local_id=local_id)
        fields: dict[str, Any] = {
            "remote_id": remote_id,
            "content_hash": content_hash,
            "last_synced_at": last_synced_at,
        }
        if existing is not None and existing.id is not None:
            update_data = {k: v for k, v in fields.items() if v is not None}
            return await self.db_ops.update(existing.id, update_data)
        return await self.db_ops.create_with_validation(
            required_fields=["user_id", "kind", "local_id", "remote_id"],
            user_id=user_id,
            kind=kind,
            local_id=local_id,
            **fields,
        )

    async def get_by_local(
        self,
        user_id: str,
        kind: SyncItemKind,
        local_id: int,
    ) -> SyncMap | None:
        """按本地条目ID查询映射."""
        return await self._find_one(user_id, kind, local_id=local_id)

    async def get_by_remote(
        self,
        user_id: str,
        kind: SyncItemKind,
        remote_id: str,
    ) -> SyncMap | None:
        """按远端对象ID查询映射."""
        return await self._find_one(user_id, kind, remote_id=remote_id)

    async def delete_by_local(
        self,
        user_id: str,
        kind: SyncItemKind,
        local_id: int,
    ) -> bool:
        """按本地条目ID删除映射.

        Returns:
            映射存在并删除返回 True

        """
        existing = await self._find_one(user_id, kind, local_id=local_id)
        if existing is None or existing.id is None:
            return False
        return await self.db_ops.delete_by_id(existing.id)

    async def delete_by_remote(
        self,
        user_id: str,
        kind: SyncItemKind,
        remote_id: str,
    ) -> bool:
        """按远端对象ID删除映射 (本地条目已硬删除时清理映射用).

        Returns:
            映射存在并删除返回 True

        """
        existing = await self._find_one(user_id, kind, remote_id=remote_id)
        if existing is None or existing.id is None:
            return False
        return await self.db_ops.delete_by_id(existing.id)

    async def list_by_kind(self, user_id: str, kind: SyncItemKind) -> list[SyncMap]:
        """列出某类型的全部映射."""
        results = await self.db_ops.find_by_filters(
            {"user_id": user_id, "kind": kind},
            limit=5000,
        )
        return list(results)

    async def _find_one(
        self,
        user_id: str,
        kind: SyncItemKind,
        *,
        local_id: int | None = None,
        remote_id: str | None = None,
    ) -> SyncMap | None:
        filters: dict[str, Any] = {"user_id": user_id, "kind": kind}
        if local_id is not None:
            filters["local_id"] = local_id
        if remote_id is not None:
            filters["remote_id"] = remote_id
        results = await self.db_ops.find_by_filters(filters, limit=1)
        return results[0] if results else None


__all__ = ["AsyncSyncMapDAO"]
