"""删除日程工具 - delete_calendar_event."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.calendar_helpers import (
    CalendarServiceAccessor,
    event_to_dict,
    json_result,
)
from src.tools.shared.tool_runtime import sync_runnable

logger = logging.getLogger(__name__)


class DeleteCalendarEventRequest(BaseModel):
    """删除日程请求."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    event_id: int = Field(..., description="日程ID(必填)")


@sync_runnable
class DeleteCalendarEventTool(BaseTool):
    """删除一条日程事件 (物理删除, 手机订阅端下次刷新后消失)."""

    name: str = "delete_calendar_event"
    search_keywords: ClassVar[list[str]] = ["删除", "取消", "移除"]
    description: str = (
        "删除一条日程事件(不可恢复, 已订阅的手机日历会在下次刷新后移除该日程).\n"
        "event_id 来自 list_calendar_events 的返回.\n\n"
        '示例: 用户: "取消周五的会" → 先 list_calendar_events 找到 event_id, 再 {"event_id": 12}'
    )
    args_schema: type[DeleteCalendarEventRequest] = DeleteCalendarEventRequest

    def _get_accessor(self) -> CalendarServiceAccessor:
        if not hasattr(self, "_calendar_acc"):
            acc = CalendarServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_calendar_acc", acc)
        return self._calendar_acc

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = DeleteCalendarEventRequest(**kwargs)

            service = await self._get_accessor().get_service()
            event = await service.get_event(request.event_id)
            if event is None:
                return json_result(
                    False,
                    f"日程不存在: event_id={request.event_id}",
                    error="日程不存在",
                )
            result = await service.delete_event(request.event_id)
            if not result.deleted:
                return json_result(False, "删除失败", error="删除失败")
            cancelled = result.cascade.cancelled_message_ids
            extra: dict[str, Any] = {
                "action": "deleted",
                "affected_event_id": request.event_id,
                "event": event_to_dict(event),
                "cancelled_message_ids": cancelled,
            }
            message = f"成功删除日程: {event.title}"
            if cancelled:
                message += (
                    f"; 已自动取消 {len(cancelled)} 条关联定时消息"
                    f"({', '.join(cancelled)})"
                )
            return json_result(True, message, **extra)
        except Exception as e:
            logger.error("删除日程失败: %s", e)
            return json_result(False, f"删除日程失败: {e!s}", error=str(e))


__all__ = ["DeleteCalendarEventTool"]
