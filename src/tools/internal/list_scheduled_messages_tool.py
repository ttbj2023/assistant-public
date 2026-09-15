"""查看待发送定时消息工具 - list_scheduled_messages."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from src.core.datetime_utils import to_user_tz
from src.tools.internal.calendar_helpers import CalendarServiceAccessor
from src.tools.internal.scheduled_message_helper import ScheduledMessageHelper
from src.tools.shared.tool_runtime import format_tool_error, sync_runnable

logger = logging.getLogger(__name__)


class ListScheduledMessagesRequest(BaseModel):
    """查看定时消息请求.

    include_failed=True 时附带列出发送失败的记录 (含失败原因).
    """

    include_failed: bool = Field(
        default=False,
        description="是否附带列出历史发送失败的记录(含失败原因), 默认False",
    )


@sync_runnable
class ListScheduledMessagesTool(BaseTool):
    """查看所有待发送的定时消息."""

    name: str = "list_scheduled_messages"
    search_keywords: ClassVar[list[str]] = ["查看", "待发送", "消息列表"]
    description: str = (
        "查看所有待发送的定时消息, 时间显示为用户本地时区. "
        "include_failed=True 时附带列出历史发送失败的记录及其失败原因."
    )
    args_schema: type[ListScheduledMessagesRequest] = ListScheduledMessagesRequest

    def _get_helper(self) -> ScheduledMessageHelper:
        if not hasattr(self, "_messenger_helper"):
            helper = ScheduledMessageHelper(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_messenger_helper", helper)
        return self._messenger_helper

    async def is_available(self) -> bool:
        return await self._get_helper().has_any_channel()

    async def _resolve_event_titles(self, event_ids: list[int]) -> dict[int, str]:
        """批量解析关联日程标题, 日程已删/查询失败时降级为只有ID."""
        if not event_ids:
            return {}
        titles: dict[int, str] = {}
        try:
            acc = CalendarServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            service = await acc.get_service()
            for eid in dict.fromkeys(event_ids):
                if eid is None:
                    continue
                event = await service.get_event_by_id(eid)
                if event is not None:
                    titles[eid] = event.title
        except Exception as e:
            logger.debug("解析关联日程标题失败(降级只显示ID): %s", e)
        return titles

    @override
    async def _arun(self, include_failed: bool = False, **kwargs: Any) -> str:
        try:
            helper = self._get_helper()
            service = await helper.get_service()
            pending = await service.list_pending_messages()

            lines: list[str] = []
            if pending:
                tz = helper.get_timezone()
                titles = await self._resolve_event_titles(
                    [m.related_event_id for m in pending if m.related_event_id],
                )
                lines.append(f"待发送消息 ({len(pending)}条, 时区: {tz}):")
                for msg in pending:
                    local_time = to_user_tz(msg.send_time, tz).strftime(
                        "%Y-%m-%d %H:%M"
                    )
                    desc = f" ({msg.description})" if msg.description else ""
                    channel_tag = f" [{msg.channel}]" if msg.channel else ""
                    related = ""
                    if msg.related_event_id:
                        title = titles.get(msg.related_event_id)
                        related = (
                            f" | 关联日程#{msg.related_event_id}: {title}"
                            if title
                            else f" | 关联日程#{msg.related_event_id}"
                        )
                    lines.append(
                        f"- [{msg.message_id}] {local_time}{channel_tag} | "
                        f"{msg.message[:80]}{desc}{related}",
                    )
            else:
                lines.append("当前没有待发送的定时消息")

            if include_failed:
                failed = await service.list_failed_messages()
                if failed:
                    tz = helper.get_timezone()
                    lines.append(f"\n发送失败消息 ({len(failed)}条, 时区: {tz}):")
                    for msg in failed:
                        local_time = to_user_tz(msg.send_time, tz).strftime(
                            "%Y-%m-%d %H:%M"
                        )
                        error = (
                            f" | 失败原因: {msg.last_error[:120]}"
                            if getattr(msg, "last_error", None)
                            else ""
                        )
                        channel_tag = f" [{msg.channel}]" if msg.channel else ""
                        lines.append(
                            f"- [{msg.message_id}] 原定 {local_time}{channel_tag} | "
                            f"{msg.message[:80]}{error}",
                        )
                else:
                    lines.append("\n没有发送失败的记录")

            return "\n".join(lines)

        except Exception as e:
            logger.error("查看定时消息失败: %s", e)
            return format_tool_error(e)


__all__ = ["ListScheduledMessagesTool"]
