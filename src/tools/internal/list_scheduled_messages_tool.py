"""查看待发送定时消息工具 - list_scheduled_messages."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from src.core.datetime_utils import to_user_tz
from src.tools.internal.scheduled_message_helper import ScheduledMessageHelper
from src.tools.shared.tool_runtime import format_tool_error, sync_runnable

logger = logging.getLogger(__name__)


class ListScheduledMessagesRequest(BaseModel):
    """查看待发送消息请求(无业务参数, 按 user/thread/agent 隔离查询)."""


@sync_runnable
class ListScheduledMessagesTool(BaseTool):
    """查看所有待发送的定时消息."""

    name: str = "list_scheduled_messages"
    search_keywords: ClassVar[list[str]] = ["查看", "待发送", "消息列表"]
    description: str = "查看所有待发送的定时消息, 时间显示为用户本地时区."
    args_schema: type[ListScheduledMessagesRequest] = ListScheduledMessagesRequest

    def _get_helper(self) -> ScheduledMessageHelper:
        if not hasattr(self, "_messenger_helper"):
            helper = ScheduledMessageHelper(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_messenger_helper", helper)
        return self._messenger_helper

    async def is_available(self) -> bool:
        return await self._get_helper().has_any_channel()

    @override
    async def _arun(self, **kwargs: Any) -> str:  # noqa: ARG002
        try:
            helper = self._get_helper()
            service = await helper.get_service()
            pending = await service.list_pending_messages()

            if not pending:
                return "当前没有待发送的定时消息"

            tz = helper.get_timezone()
            lines = [f"待发送消息 ({len(pending)}条, 时区: {tz}):"]
            for msg in pending:
                local_time = to_user_tz(msg.send_time, tz).strftime("%Y-%m-%d %H:%M")
                desc = f" ({msg.description})" if msg.description else ""
                channel_tag = f" [{msg.channel}]" if msg.channel else ""
                lines.append(
                    f"- [{msg.message_id}] {local_time}{channel_tag} | "
                    f"{msg.message[:80]}{desc}",
                )
            return "\n".join(lines)

        except Exception as e:
            logger.error("查看定时消息失败: %s", e)
            return format_tool_error(e)


__all__ = ["ListScheduledMessagesTool"]
