"""用户级日历事件数据访问对象.

提供日历事件 CRUD + 时间范围重叠查询 + UID 查询.

时间范围查询语义: 事件与窗口有交集即返回, 条件为
(start_time < window_end AND end_time >= window_start) OR 重复事件豁免
(recurrence_rule 非空且未过期, 由服务层展开后精确定位).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select

from src.storage.models.calendar_event import CalendarEvent, CalendarEventStatus

from .database_operations import AsyncDatabaseOperations

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class AsyncCalendarEventDAO:
    """用户级日历事件 DAO."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.db_ops = AsyncDatabaseOperations(session_factory, CalendarEvent)
        self.session_factory = session_factory

    async def create_event(self, **kwargs: Any) -> CalendarEvent:
        """创建日程事件.

        Args:
            **kwargs: CalendarEvent 字段 (title/user_id/start_time/end_time 必填)

        Returns:
            持久化后的日程事件

        """
        return await self.db_ops.create_with_validation(
            required_fields=["title", "user_id", "start_time", "end_time"],
            **kwargs,
        )

    async def get_event_by_id(self, event_id: int) -> CalendarEvent | None:
        """按主键查询日程事件."""
        return await self.db_ops.get_by_id(event_id)

    async def find_by_uid(self, event_uid: str) -> CalendarEvent | None:
        """按 event_uid 精确查询."""
        try:
            results = await self.db_ops.find_by_filters(
                {"event_uid": event_uid},
                limit=1,
            )
            return results[0] if results else None
        except Exception as e:
            logger.error("按 event_uid 查询日程失败: %s", e)
            raise

    async def update_event(
        self,
        event_id: int,
        update_data: dict[str, Any],
    ) -> CalendarEvent | None:
        """更新日程事件."""
        return await self.db_ops.update(event_id, update_data)

    async def delete_event(self, event_id: int) -> bool:
        """删除日程事件."""
        return await self.db_ops.delete_by_id(event_id)

    async def list_by_time_range(
        self,
        user_id: str,
        window_start: datetime,
        window_end: datetime,
        limit: int = 500,
    ) -> list[CalendarEvent]:
        """查询与时间窗口重叠的日程事件 (含活跃重复事件).

        Args:
            user_id: 用户ID
            window_start: 窗口起点 (aware UTC)
            window_end: 窗口终点 (aware UTC)
            limit: 返回数量上限

        Returns:
            重叠的单次事件 + 原始 start 在窗口前的活跃重复事件

        """
        try:
            async with self.session_factory() as session:
                overlap = (CalendarEvent.start_time < window_end) & (
                    CalendarEvent.end_time >= window_start
                )
                recurring = (CalendarEvent.recurrence_rule.is_not(None)) & (
                    or_(
                        CalendarEvent.recurrence_until.is_(None),
                        CalendarEvent.recurrence_until >= window_start,
                    )
                )
                stmt = (
                    select(CalendarEvent)
                    .where(
                        CalendarEvent.user_id == user_id,
                        CalendarEvent.status == CalendarEventStatus.ACTIVE,
                        or_(overlap, recurring),
                    )
                    .order_by(CalendarEvent.start_time)
                    .limit(limit)
                )
                result = await session.execute(stmt)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("时间范围查询日程失败: %s", e)
            raise

    async def list_active(self, user_id: str, limit: int = 1000) -> list[CalendarEvent]:
        """列出用户全部活跃日程 (订阅 feed / 全量展开用)."""
        try:
            return await self.db_ops.find_by_filters(
                {"user_id": user_id, "status": CalendarEventStatus.ACTIVE},
                limit=limit,
            )
        except Exception as e:
            logger.error("列出活跃日程失败: %s", e)
            raise

    async def health_check(self) -> bool:
        """健康检查."""
        return await self.db_ops.health_check()
