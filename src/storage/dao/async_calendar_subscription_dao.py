"""用户级日历订阅 DAO.

订阅 token 只存 sha256 哈希; 撤销语义为 revoked_at 非 None.
按 subscription_id 的更新先反查主键再走通用 update.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.core.datetime_utils import now_utc
from src.storage.models.calendar_subscription import CalendarSubscription

from .database_operations import AsyncDatabaseOperations

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class AsyncCalendarSubscriptionDAO:
    """用户级日历订阅 DAO."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.db_ops = AsyncDatabaseOperations(session_factory, CalendarSubscription)
        self.session_factory = session_factory

    async def create_subscription(
        self,
        *,
        user_id: str,
        token_hash: str,
        subscription_id: str,
    ) -> CalendarSubscription:
        """创建订阅记录."""
        return await self.db_ops.create_with_validation(
            required_fields=["user_id", "token_hash"],
            user_id=user_id,
            token_hash=token_hash,
            subscription_id=subscription_id,
        )

    async def find_active_by_hash(
        self,
        token_hash: str,
    ) -> CalendarSubscription | None:
        """按哈希查询未撤销的订阅."""
        try:
            results = await self.db_ops.find_by_filters(
                {"token_hash": token_hash},
                limit=10,
            )
            active = [sub for sub in results if sub.revoked_at is None]
            return active[0] if active else None
        except Exception as e:
            logger.error("按哈希查询订阅失败: %s", e)
            raise

    async def get_by_subscription_id(
        self,
        subscription_id: str,
    ) -> CalendarSubscription | None:
        """按订阅标识查询."""
        try:
            results = await self.db_ops.find_by_filters(
                {"subscription_id": subscription_id},
                limit=1,
            )
            return results[0] if results else None
        except Exception as e:
            logger.error("按订阅标识查询失败: %s", e)
            raise

    async def list_by_user(self, user_id: str) -> list[CalendarSubscription]:
        """列出用户全部订阅 (含已撤销)."""
        try:
            return await self.db_ops.find_by_filters(
                {"user_id": user_id},
                limit=100,
            )
        except Exception as e:
            logger.error("列出用户订阅失败: %s", e)
            raise

    async def revoke_subscription(self, subscription_id: str) -> bool:
        """撤销订阅 (反查主键后设置 revoked_at)."""
        record = await self.get_by_subscription_id(subscription_id)
        if record is None or record.id is None:
            return False
        updated = await self.db_ops.update(record.id, {"revoked_at": now_utc()})
        return updated is not None

    async def touch(
        self,
        subscription_id: str,
        update_data: dict[str, Any],
    ) -> CalendarSubscription | None:
        """按订阅标识更新记录 (拉取时间戳)."""
        record = await self.get_by_subscription_id(subscription_id)
        if record is None or record.id is None:
            return None
        return await self.db_ops.update(record.id, update_data)
