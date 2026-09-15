"""取消定时消息工具 - cancel_scheduled_message."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.scheduled_message_helper import ScheduledMessageHelper
from src.tools.shared.tool_runtime import format_tool_error, sync_runnable

logger = logging.getLogger(__name__)


class CancelScheduledMessageRequest(BaseModel):
    """取消定时消息请求."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    message_id: str = Field(
        ...,
        description="要取消的消息ID, 来自 list_scheduled_messages 的返回",
    )


@sync_runnable
class CancelScheduledMessageTool(BaseTool):
    """取消一条待发送的定时消息."""

    name: str = "cancel_scheduled_message"
    search_keywords: ClassVar[list[str]] = ["取消", "撤销"]
    description: str = (
        "取消一条待发送的定时消息, 只有尚未发送(pending)的消息可取消.\n"
        "message_id 来自 list_scheduled_messages 的返回.\n"
        '示例: 用户说"取消提醒吃药那条定时消息" → 先 list_scheduled_messages 找到该消息的 '
        'message_id, 再 {"message_id": "..."}'
    )
    args_schema: type[CancelScheduledMessageRequest] = CancelScheduledMessageRequest

    def _get_helper(self) -> ScheduledMessageHelper:
        if not hasattr(self, "_messenger_helper"):
            helper = ScheduledMessageHelper(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_messenger_helper", helper)
        return self._messenger_helper

    async def is_available(self) -> bool:
        return await self._get_helper().has_any_channel()

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = CancelScheduledMessageRequest(**kwargs)
            service = await self._get_helper().get_service()
            success = await service.cancel_message(request.message_id)

            if success:
                return f"✅ 定时消息 {request.message_id} 已取消"
            return f"取消消息 {request.message_id} 失败"

        except Exception as e:
            logger.error("取消定时消息失败: %s", e)
            return format_tool_error(e)


__all__ = ["CancelScheduledMessageTool"]
