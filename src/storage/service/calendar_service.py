"""用户级日历事件业务服务.

提供日程 CRUD / 归属校验 / 时间范围查询, 供 Agent 工具与 REST API 共用.
时间一律 aware UTC; 重复事件的展开由上层 (rrule_utils) 完成, 本服务返回原始事件.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, override

from sqlalchemy import text

from src.calendar.subscription import (
    generate_subscription_token,
    hash_subscription_token,
)
from src.core.datetime_utils import now_utc
from src.storage.dao.async_calendar_event_dao import AsyncCalendarEventDAO
from src.storage.dao.async_calendar_subscription_dao import (
    AsyncCalendarSubscriptionDAO,
)
from src.storage.models.calendar_event import CalendarEvent, CalendarEventStatus
from src.storage.models.calendar_subscription import CalendarSubscription

from .health_check_mixin import ServiceHealthCheckMixin

logger = logging.getLogger(__name__)


@dataclass
class ShadowCascadeReport:
    """日程写操作引发的定时消息影子级联结果 (如实汇报用)."""

    cancelled_message_ids: list[str] = field(default_factory=list)
    rescheduled_message_ids: list[str] = field(default_factory=list)
    linked_pending_count: int = 0


@dataclass
class EventUpdateResult:
    """update_event 结果: 更新后日程 + 影子级联报告."""

    event: CalendarEvent | None
    cascade: ShadowCascadeReport = field(default_factory=ShadowCascadeReport)


@dataclass
class EventDeleteResult:
    """delete_event 结果: 是否删除 + 影子级联报告."""

    deleted: bool
    cascade: ShadowCascadeReport = field(default_factory=ShadowCascadeReport)


def _notify_graph_sync(user_id: str) -> None:
    """本地写后投递 Graph 同步即时信号 (惰性 import 防包初始化循环)."""
    from src.sync.graph_sync_engine import notify_local_write

    notify_local_write(user_id)


# last_access_at 写入节流间隔, 避免手机高频轮询打满写放大
_TOUCH_THROTTLE = timedelta(hours=1)


class CalendarService(ServiceHealthCheckMixin):
    """用户级日历事件业务服务."""

    def __init__(self, session_factory: Callable[[], Any], user_id: str) -> None:
        """初始化日历服务.

        Args:
            session_factory: SQLAlchemy异步会话工厂
            user_id: 用户ID (用户级数据归属)

        """
        super().__init__()
        self.session_factory = session_factory
        self.user_id = user_id
        self.logger = logging.getLogger(f"{__name__}.CalendarService")
        self.dao = AsyncCalendarEventDAO(session_factory)
        self.subscription_dao = AsyncCalendarSubscriptionDAO(session_factory)

    async def create_event(
        self,
        title: str,
        start_time: datetime,
        end_time: datetime,
        *,
        description: str | None = None,
        location: str | None = None,
        all_day: bool = False,
        recurrence_rule: str | None = None,
        recurrence_until: datetime | None = None,
        source_thread_id: str | None = None,
        source_agent_id: str | None = None,
    ) -> CalendarEvent:
        """创建日程事件.

        Args:
            title: 日程标题
            start_time: 开始时间 (aware UTC)
            end_time: 结束时间 (aware UTC)
            description: 描述
            location: 地点
            all_day: 是否全天事件
            recurrence_rule: RRULE 字符串
            recurrence_until: 重复截止时间
            source_thread_id: 创建线程 (溯源)
            source_agent_id: 创建 Agent (溯源)

        Returns:
            持久化后的日程事件

        Raises:
            RuntimeError: 数据库操作失败

        """
        try:
            event = await self.dao.create_event(
                title=title,
                user_id=self.user_id,
                start_time=start_time,
                end_time=end_time,
                description=description,
                location=location,
                all_day=all_day,
                recurrence_rule=recurrence_rule,
                recurrence_until=recurrence_until,
                source_thread_id=source_thread_id,
                source_agent_id=source_agent_id,
            )
            _notify_graph_sync(self.user_id)  # 投递 Graph 同步即时信号
            return event
        except Exception as e:
            self.logger.error(
                "❌ 创建日程失败 - user_id: %s, error: %s", self.user_id, e
            )
            raise RuntimeError(f"创建日程失败: {e}") from e

    async def get_event(self, event_id: int) -> CalendarEvent | None:
        """按 ID 查询日程 (校验归属).

        Args:
            event_id: 日程ID

        Returns:
            归属当前用户的日程, 无或非本人返回 None

        Raises:
            RuntimeError: 数据库操作失败

        """
        try:
            event = await self.dao.get_event_by_id(event_id)
            if event is None or event.user_id != self.user_id:
                return None
            return event
        except Exception as e:
            self.logger.error("❌ 查询日程失败 - event_id: %s, error: %s", event_id, e)
            raise RuntimeError(f"查询日程失败: {e}") from e

    async def update_event(
        self,
        event_id: int,
        update_data: dict[str, Any],
    ) -> EventUpdateResult:
        """更新日程 (校验归属 + 时间对合并校验 + 影子级联).

        开始时间变更时按偏移自动顺延关联的定时消息 (影子跟随本体);
        仅改非时间字段时不级联, 报告关联影子数量供上层如实汇报.

        Args:
            event_id: 日程ID
            update_data: 待更新字段

        Returns:
            EventUpdateResult: 更新后日程 (无/非本人为 None) + 级联报告

        Raises:
            RuntimeError: 校验失败或数据库操作失败

        """
        try:
            existing = await self.dao.get_event_by_id(event_id)
            if existing is None or existing.user_id != self.user_id:
                return EventUpdateResult(None)

            # setattr 单字段更新不触发 before 校验器, 时间对需在服务层合并校验
            effective_start = update_data.get("start_time", existing.start_time)
            effective_end = update_data.get("end_time", existing.end_time)
            if effective_end < effective_start:
                raise RuntimeError("结束时间不能早于开始时间")

            updated = await self.dao.update_event(event_id, update_data)
            _notify_graph_sync(self.user_id)  # 投递 Graph 同步即时信号

            cascade = ShadowCascadeReport()
            from .scheduled_message_cascade import (
                count_pending_shadows,
                reschedule_shadows_for_event,
            )

            if effective_start != existing.start_time:
                # 影子顺延: 保持与旧日程的相对偏移 (如"提前10分钟")
                delta = effective_start - existing.start_time
                cascade.rescheduled_message_ids = await reschedule_shadows_for_event(
                    self.user_id, event_id, delta
                )
            else:
                cascade.linked_pending_count = await count_pending_shadows(
                    self.user_id, event_id
                )
            return EventUpdateResult(updated, cascade)
        except RuntimeError:
            raise
        except Exception as e:
            self.logger.error("❌ 更新日程失败 - event_id: %s, error: %s", event_id, e)
            raise RuntimeError(f"更新日程失败: {e}") from e

    async def delete_event(self, event_id: int) -> EventDeleteResult:
        """删除日程 (校验归属 + 影子级联取消).

        删除日程时自动取消关联的 pending 定时消息 (为已删日程推送提醒
        属于用户视角的 bug), 级联结果进报告供上层如实汇报.

        Args:
            event_id: 日程ID

        Returns:
            EventDeleteResult: 是否删除 + 级联报告

        Raises:
            RuntimeError: 数据库操作失败

        """
        try:
            existing = await self.dao.get_event_by_id(event_id)
            if existing is None or existing.user_id != self.user_id:
                return EventDeleteResult(False)
            deleted = await self.dao.delete_event(event_id)
            cascade = ShadowCascadeReport()
            if deleted:
                _notify_graph_sync(self.user_id)  # 投递 Graph 同步即时信号
                from .scheduled_message_cascade import cancel_shadows_for_event

                cascade.cancelled_message_ids = await cancel_shadows_for_event(
                    self.user_id, event_id
                )
            return EventDeleteResult(deleted, cascade)
        except Exception as e:
            self.logger.error("❌ 删除日程失败 - event_id: %s, error: %s", event_id, e)
            raise RuntimeError(f"删除日程失败: {e}") from e

    async def cancel_event(self, event_id: int) -> CalendarEvent | None:
        """软取消日程 (状态置为 cancelled, 订阅 feed 不再输出).

        Args:
            event_id: 日程ID

        Returns:
            更新后的日程, 无或非本人返回 None

        """
        return await self.update_event(
            event_id, {"status": CalendarEventStatus.CANCELLED}
        )

    async def list_events_in_range(
        self,
        window_start: datetime,
        window_end: datetime,
    ) -> list[CalendarEvent]:
        """查询与时间窗口重叠的日程 (含活跃重复事件原始记录).

        Args:
            window_start: 窗口起点 (aware UTC)
            window_end: 窗口终点 (aware UTC)

        Returns:
            重叠日程列表

        Raises:
            RuntimeError: 数据库操作失败

        """
        try:
            return await self.dao.list_by_time_range(
                self.user_id,
                window_start,
                window_end,
            )
        except Exception as e:
            self.logger.error(
                "❌ 范围查询日程失败 - user_id: %s, error: %s",
                self.user_id,
                e,
            )
            raise RuntimeError(f"范围查询日程失败: {e}") from e

    async def create_subscription(self) -> tuple[CalendarSubscription, str]:
        """创建新的订阅 token (明文仅本次返回).

        Returns:
            (订阅记录, 明文 token) 二元组

        Raises:
            RuntimeError: 数据库操作失败

        """
        token = generate_subscription_token()
        try:
            record = await self.subscription_dao.create_subscription(
                user_id=self.user_id,
                token_hash=hash_subscription_token(token),
                subscription_id=f"sub_{token[4:12]}",
            )
            return record, token
        except Exception as e:
            self.logger.error(
                "❌ 创建订阅失败 - user_id: %s, error: %s", self.user_id, e
            )
            raise RuntimeError(f"创建订阅失败: {e}") from e

    async def verify_subscription_token(
        self,
        token: str,
    ) -> CalendarSubscription | None:
        """校验订阅 token, 返回未撤销的订阅记录.

        Args:
            token: 明文 token

        Returns:
            有效订阅记录, 无效或已撤销返回 None

        """
        try:
            return await self.subscription_dao.find_active_by_hash(
                hash_subscription_token(token),
            )
        except Exception as e:
            self.logger.error("❌ 校验订阅失败: %s", e)
            return None

    async def revoke_subscription(self, subscription_id: str) -> bool:
        """撤销订阅 (校验归属).

        Args:
            subscription_id: 订阅标识

        Returns:
            是否撤销成功

        """
        try:
            record = await self.subscription_dao.get_by_subscription_id(subscription_id)
            if record is None or record.user_id != self.user_id:
                return False
            return await self.subscription_dao.revoke_subscription(subscription_id)
        except Exception as e:
            self.logger.error(
                "❌ 撤销订阅失败 - subscription_id: %s, error: %s",
                subscription_id,
                e,
            )
            raise RuntimeError(f"撤销订阅失败: {e}") from e

    async def list_subscriptions(self) -> list[CalendarSubscription]:
        """列出当前用户全部订阅 (含已撤销).

        Returns:
            订阅记录列表

        Raises:
            RuntimeError: 数据库操作失败

        """
        try:
            return await self.subscription_dao.list_by_user(self.user_id)
        except Exception as e:
            self.logger.error(
                "❌ 列出订阅失败 - user_id: %s, error: %s", self.user_id, e
            )
            raise RuntimeError(f"列出订阅失败: {e}") from e

    async def touch_subscription(self, subscription: CalendarSubscription) -> None:
        """更新订阅拉取时间戳 (节流: 间隔不足 1 小时跳过).

        Args:
            subscription: 订阅记录

        """
        now = now_utc()
        if (
            subscription.last_access_at is not None
            and subscription.last_access_at.tzinfo is None
        ):
            # SQLite naive 读出视为 UTC
            subscription.last_access_at = subscription.last_access_at.replace(
                tzinfo=UTC,
            )
        if (
            subscription.last_access_at is not None
            and now - subscription.last_access_at < _TOUCH_THROTTLE
        ):
            return
        try:
            await self.subscription_dao.touch(
                subscription.subscription_id,
                {"last_access_at": now},
            )
        except Exception as e:
            # 拉取时间戳是可观测性指标, 失败不影响 feed 输出
            self.logger.warning("更新订阅拉取时间失败: %s", e)

    @override
    async def _check_service_health(self) -> dict[str, Any]:
        """检查日历服务健康状态."""
        try:
            async with self.session_factory() as session:
                await session.execute(text("SELECT 1"))

            return {
                "status": "healthy",
                "database_connected": True,
                "statistics": {"user_id": self.user_id},
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "database_connected": False,
                "error": str(e),
            }
