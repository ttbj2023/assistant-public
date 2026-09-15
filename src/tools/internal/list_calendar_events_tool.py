"""查询日程工具 - list_calendar_events."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.calendar.rrule_utils import expand_events_in_range
from src.core.datetime_utils import now_utc, to_user_tz
from src.tools.internal.calendar_helpers import (
    CalendarServiceAccessor,
    _get_user_timezone,
    json_result,
    parse_date_only,
)
from src.tools.shared.tool_runtime import sync_runnable

logger = logging.getLogger(__name__)


class ListCalendarEventsRequest(BaseModel):
    """查询日程请求."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    date: str | None = Field(
        None,
        description="查询起始日期, ISO格式如 2026-09-20. 省略默认今天",
    )
    days: int = Field(
        default=1,
        ge=1,
        le=365,
        description="查询天数, 1=单日, 7=一周, 最大365(一年). 重复日程会展开为具体实例",
    )


@sync_runnable
class ListCalendarEventsTool(BaseTool):
    """查询日程列表 (重复日程展开为具体实例)."""

    name: str = "list_calendar_events"
    search_keywords: ClassVar[list[str]] = ["日程", "日历", "安排", "行程", "今天"]
    description: str = (
        "查询日程列表, 重复日程(如每周例会)会展开为具体实例.\n"
        "当用户询问某天/某段时间的安排时使用.\n\n"
        "示例:\n"
        '- 用户: "我今天有什么安排" → {} (省略date默认从今天开始)\n'
        '- 用户: "这周的日程" → {"days": 7}'
    )
    args_schema: type[ListCalendarEventsRequest] = ListCalendarEventsRequest

    def _get_accessor(self) -> CalendarServiceAccessor:
        if not hasattr(self, "_calendar_acc"):
            acc = CalendarServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_calendar_acc", acc)
        return self._calendar_acc

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = ListCalendarEventsRequest(**kwargs)
            timezone = _get_user_timezone()
            try:
                window_start = parse_date_only(
                    request.date
                    or to_user_tz(now_utc(), timezone).strftime("%Y-%m-%d"),
                    timezone=timezone,
                )
            except ValueError as e:
                return json_result(False, str(e), error=str(e))

            window_end = window_start + timedelta(days=request.days)

            service = await self._get_accessor().get_service()
            events = await service.list_events_in_range(window_start, window_end)
            instances = expand_events_in_range(events, window_start, window_end)

            formatted = [
                {
                    "event_id": item["event_id"],
                    "title": item["title"],
                    "start_time": to_user_tz(item["start_time"], timezone).isoformat(),
                    "end_time": to_user_tz(item["end_time"], timezone).isoformat(),
                    "all_day": item["all_day"],
                    "location": item["location"],
                    "is_recurring_instance": item["is_recurring_instance"],
                }
                for item in instances
            ]
            return json_result(
                True,
                f"找到 {len(formatted)} 条日程",
                count=len(formatted),
                events=formatted,
            )
        except Exception as e:
            logger.error("查询日程失败: %s", e)
            return json_result(False, f"查询日程失败: {e!s}", error=str(e))


__all__ = ["ListCalendarEventsTool"]
