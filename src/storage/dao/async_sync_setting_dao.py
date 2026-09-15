"""Graph 同步键值设置 DAO."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.storage.models.sync_setting import SyncSetting

from .database_operations import AsyncDatabaseOperations

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class AsyncSyncSettingDAO:
    """用户级 Graph 同步键值设置 DAO."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.db_ops = AsyncDatabaseOperations(session_factory, SyncSetting)
        self.session_factory = session_factory

    async def get_value(self, user_id: str, key: str) -> str | None:
        """读取设置值.

        Args:
            user_id: 用户ID
            key: 设置键

        Returns:
            设置值, 不存在返回 None

        """
        results = await self.db_ops.find_by_filters(
            {"user_id": user_id, "key": key},
            limit=1,
        )
        return results[0].value if results else None

    async def set_value(self, user_id: str, key: str, value: str) -> SyncSetting:
        """写入设置值 (存在即更新).

        Args:
            user_id: 用户ID
            key: 设置键
            value: 设置值

        Returns:
            持久化后的设置记录

        """
        results = await self.db_ops.find_by_filters(
            {"user_id": user_id, "key": key},
            limit=1,
        )
        if results:
            existing = results[0]
            if existing.id is not None:
                return await self.db_ops.update(existing.id, {"value": value})
        return await self.db_ops.create(user_id=user_id, key=key, value=value)

    async def delete_value(self, user_id: str, key: str) -> bool:
        """删除设置值 (回退全局默认用).

        Args:
            user_id: 用户ID
            key: 设置键

        Returns:
            设置存在并删除返回 True, 不存在返回 False

        """
        results = await self.db_ops.find_by_filters(
            {"user_id": user_id, "key": key},
            limit=1,
        )
        if not results:
            return False
        existing = results[0]
        if existing.id is None:
            return False
        return await self.db_ops.delete_by_id(existing.id)


__all__ = ["AsyncSyncSettingDAO"]
