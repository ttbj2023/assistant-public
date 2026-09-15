"""更新日程工具 - update_calendar_event."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.calendar_helpers import (
    CalendarServiceAccessor,
    _get_user_timezone,
    event_to_dict,
    json_result,
    parse_event_datetime,
    parse_recurrence_freq,
)
from src.tools.shared.tool_runtime import sync_runnable

logger = logging.getLogger(__name__)


class UpdateCalendarEventRequest(BaseModel):
    """更新日程请求 (仅显式提供的字段)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    event_id: int = Field(..., description="日程ID(必填)")
    title: str | None = Field(None, description="新标题")
    description: str | None = Field(None, description="新描述")
    location: str | None = Field(None, description="新地点")
    start_time: str | None = Field(None, description="新开始时间, ISO格式")
    end_time: str | None = Field(None, description="新结束时间, ISO格式")
    recurrence_freq: str | None = Field(
        None,
        description="新重复频率: daily/weekly/monthly/yearly; 传 'none' 取消重复",
    )
    recurrence_interval: int = Field(default=1, ge=1, le=30, description="重复间隔倍数")
    recurrence_until: str | None = Field(None, description="新重复截止日期, ISO格式")


@sync_runnable
class UpdateCalendarEventTool(BaseTool):
    """更新一条日程事件."""

    name: str = "update_calendar_event"
    search_keywords: ClassVar[list[str]] = ["修改", "更新", "改期", "调整"]
    description: str = (
        "更新一条日程事件, 仅更新显式传入的字段.\n"
        "event_id 来自 list_calendar_events 的返回.\n\n"
        "示例:\n"
        '- 用户: "把明天的会改到下午3点" → {"event_id": 12, "start_time": "2026-09-21T15:00:00"}\n'
        '- 用户: "周会改成每两周一次" → '
        '{"event_id": 12, "recurrence_freq": "weekly", "recurrence_interval": 2}'
    )
    args_schema: type[UpdateCalendarEventRequest] = UpdateCalendarEventRequest

    def _get_accessor(self) -> CalendarServiceAccessor:
        if not hasattr(self, "_calendar_acc"):
            acc = CalendarServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_calendar_acc", acc)
        return self._calendar_acc

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = UpdateCalendarEventRequest(**kwargs)
            timezone = _get_user_timezone()

            update_data: dict[str, Any] = {}
            for field_name in ("title", "description", "location"):
                value = getattr(request, field_name)
                if value is not None:
                    update_data[field_name] = value
            for field_name in ("start_time", "end_time", "recurrence_until"):
                value = getattr(request, field_name)
                if value is not None:
                    all_day = False  # 更新时保留原 all_day 语义, 由服务层合并校验
                    update_data[field_name] = parse_event_datetime(
                        value,
                        timezone=timezone,
                        all_day=all_day,
                    )
            if request.recurrence_freq is not None:
                if request.recurrence_freq.strip().lower() == "none":
                    update_data["recurrence_rule"] = None
                else:
                    update_data["recurrence_rule"] = parse_recurrence_freq(
                        request.recurrence_freq,
                        interval=request.recurrence_interval,
                    )

            if not update_data:
                return json_result(
                    False, "未提供任何待更新字段", error="未提供任何待更新字段"
                )

            service = await self._get_accessor().get_service()
            result = await service.update_event(request.event_id, update_data)
            event = result.event
            if event is None:
                return json_result(
                    False,
                    f"日程不存在: event_id={request.event_id}",
                    error="日程不存在",
                )
            cascade = result.cascade
            cascade_lines: list[str] = []
            if cascade.rescheduled_message_ids:
                cascade_lines.append(
                    f"已同步顺延 {len(cascade.rescheduled_message_ids)} 条"
                    f"关联定时消息: {', '.join(cascade.rescheduled_message_ids)}"
                )
            elif cascade.linked_pending_count > 0:
                cascade_lines.append(
                    f"存在 {cascade.linked_pending_count} 条关联定时消息"
                    "(仅改非时间字段未自动调整, 消息内容/时间需手动同步)"
                )
            extra: dict[str, Any] = {
                "action": "updated",
                "affected_event_id": event.id,
                "event": event_to_dict(event),
                "cascade": cascade_lines,
            }
            message = f"成功更新日程: {event.title}"
            if cascade_lines:
                message += "; " + "; ".join(cascade_lines)
            return json_result(True, message, **extra)
        except Exception as e:
            logger.error("更新日程失败: %s", e)
            return json_result(False, f"更新日程失败: {e!s}", error=str(e))


__all__ = ["UpdateCalendarEventTool"]
