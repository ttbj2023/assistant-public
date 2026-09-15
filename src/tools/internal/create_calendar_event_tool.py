"""创建日程工具 - create_calendar_event."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.calendar_helpers import (
    CalendarServiceAccessor,
    event_to_dict,
    json_result,
    parse_event_datetime,
    parse_recurrence_freq,
)
from src.tools.shared.tool_runtime import sync_runnable

logger = logging.getLogger(__name__)


class CreateCalendarEventRequest(BaseModel):
    """创建日程请求."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    title: str = Field(..., description="日程标题(必填)")
    start_time: str = Field(
        ...,
        description="开始时间, ISO格式. 定时事件如 2026-09-20T10:00:00; 全天事件如 2026-09-20",
    )
    end_time: str | None = Field(
        None,
        description="结束时间, ISO格式. 省略时: 定时事件默认开始后1小时, 全天事件默认当天",
    )
    all_day: bool = Field(default=False, description="是否全天事件")
    location: str | None = Field(None, description="地点")
    description: str | None = Field(None, description="日程描述/备注")
    recurrence_freq: str | None = Field(
        None,
        description="重复频率: daily/weekly/monthly/yearly (或 每天/每周/每月/每年). 不传为单次日程",
    )
    recurrence_interval: int = Field(
        default=1,
        ge=1,
        le=30,
        description="重复间隔倍数, 如 weekly+interval=2 表示每两周",
    )
    recurrence_until: str | None = Field(
        None,
        description="重复截止日期, ISO格式如 2026-12-31. 省略则一直重复",
    )


@sync_runnable
class CreateCalendarEventTool(BaseTool):
    """创建一条日程事件."""

    name: str = "create_calendar_event"
    search_keywords: ClassVar[list[str]] = ["日程", "日历", "安排", "会议", "行程"]
    description: str = (
        "创建一条日程事件(支持重复日程).\n"
        "当用户要安排日程/会议/行程时使用, 必须提供 title 和 start_time; "
        "其他字段仅在用户明确提及时才传入.\n"
        "日程仅记录不主动推送通知; 需到点主动提醒用户时用定时消息工具.\n\n"
        "示例:\n"
        '- 用户: "帮我约个会, 周五上午10点, 会议室A" → '
        '{"title": "会议", "start_time": "2026-09-18T10:00:00", "end_time": "2026-09-18T11:00:00", '
        '"location": "会议室A"}\n'
        '- 用户: "每周一上午9点站会" → '
        '{"title": "站会", "start_time": "2026-09-21T09:00:00", "recurrence_freq": "weekly"}\n'
        '- 用户: "10月1日出去玩一天" → '
        '{"title": "出游", "start_time": "2026-10-01", "all_day": true}'
    )
    args_schema: type[CreateCalendarEventRequest] = CreateCalendarEventRequest

    def _get_accessor(self) -> CalendarServiceAccessor:
        if not hasattr(self, "_calendar_acc"):
            acc = CalendarServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_calendar_acc", acc)
        return self._calendar_acc

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = CreateCalendarEventRequest(**kwargs)
            title = request.title.strip()
            if not title:
                return json_result(False, "日程标题不能为空", error="日程标题不能为空")

            timezone = self._get_timezone()
            start_time = parse_event_datetime(
                request.start_time,
                timezone=timezone,
                all_day=request.all_day,
            )
            if request.end_time:
                end_time = parse_event_datetime(
                    request.end_time,
                    timezone=timezone,
                    all_day=request.all_day,
                )
            elif request.all_day:
                end_time = start_time
            else:
                from datetime import timedelta

                end_time = start_time + timedelta(hours=1)

            recurrence_rule = parse_recurrence_freq(
                request.recurrence_freq,
                interval=request.recurrence_interval,
            )
            recurrence_until = None
            if request.recurrence_until:
                recurrence_until = parse_event_datetime(
                    request.recurrence_until,
                    timezone=timezone,
                    all_day=True,
                )

            service = await self._get_accessor().get_service()
            event = await service.create_event(
                title=title,
                start_time=start_time,
                end_time=end_time,
                description=request.description,
                location=request.location,
                all_day=request.all_day,
                recurrence_rule=recurrence_rule,
                recurrence_until=recurrence_until,
                source_thread_id=self.thread_id,
                source_agent_id=self.agent_id,
            )
            extra: dict[str, Any] = {
                "action": "created",
                "affected_event_id": event.id,
                "event": event_to_dict(event),
                "reminder_hint": (
                    f"日程仅记录不主动推送; 如需到点主动提醒, "
                    f"调用定时消息工具(schedule_message_wechat/email)并传 "
                    f"related_event_id={event.id}"
                ),
            }
            return json_result(True, f"成功创建日程: {event.title}", **extra)
        except Exception as e:
            logger.error("创建日程失败: %s", e)
            return json_result(False, f"创建日程失败: {e!s}", error=str(e))

    def _get_timezone(self) -> str:
        from src.tools.internal.calendar_helpers import _get_user_timezone

        return _get_user_timezone()


__all__ = ["CreateCalendarEventTool"]
