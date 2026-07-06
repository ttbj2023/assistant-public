"""查看待发送定时消息工具 - list_scheduled_messages."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from pydantic import BaseModel

from src.tools.internal.scheduled_messenger_base import ScheduledMessengerBase

logger = logging.getLogger(__name__)


class ListScheduledMessagesRequest(BaseModel):
    """查看待发送消息请求(无业务参数, 按 user/thread/agent 隔离查询)."""


class ListScheduledMessagesTool(ScheduledMessengerBase):
    """查看所有待发送的定时消息."""

    name: str = "list_scheduled_messages"
    search_keywords: ClassVar[list[str]] = ["查看", "待发送", "消息列表"]
    description: str = "查看所有待发送的定时消息."
    args_schema: type[ListScheduledMessagesRequest] = ListScheduledMessagesRequest

    @override
    def _apply_description(self, *, has_wechat: bool, has_email: bool) -> None:
        self.description = "查看所有待发送的定时消息."

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            service = await self._get_service()
            pending = await service.list_pending_messages()

            if not pending:
                return "当前没有待发送的定时消息"

            lines = [f"待发送消息 ({len(pending)}条):"]
            for msg in pending:
                local_time = msg.send_time.strftime("%Y-%m-%d %H:%M")
                desc = f" ({msg.description})" if msg.description else ""
                channel_tag = f" [{msg.channel}]" if msg.channel else ""
                lines.append(
                    f"- [{msg.message_id}] {local_time}{channel_tag} | "
                    f"{msg.message[:80]}{desc}",
                )
            return "\n".join(lines)

        except Exception as e:
            logger.error("查看定时消息失败: %s", e)
            return self._format_error(e)


__all__ = ["ListScheduledMessagesTool"]
