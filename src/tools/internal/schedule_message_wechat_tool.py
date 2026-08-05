"""定时消息(微信渠道) - schedule_message_wechat."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, ClassVar, override
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.scheduled_message_helper import ScheduledMessageHelper
from src.tools.shared.tool_runtime import format_tool_error, sync_runnable

logger = logging.getLogger(__name__)


class ScheduleMessageWechatRequest(BaseModel):
    """微信定时消息请求."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    message: str = Field(..., description="消息内容, 最长2000字符")
    send_time: str = Field(
        ...,
        description=(
            "发送时间, ISO 8601格式且应携带时区偏移, "
            "如 2026-05-30T08:00:00+08:00 或 2026-05-30T00:00:00Z. "
            "不带时区时按用户本地时区解释"
        ),
    )
    description: str | None = Field(
        None,
        description="备注说明, 如 '提醒用户吃药'",
    )


@sync_runnable
class ScheduleMessageWechatTool(BaseTool):
    """通过微信创建定时消息/提醒."""

    name: str = "schedule_message_wechat"
    search_keywords: ClassVar[list[str]] = [
        "定时",
        "提醒",
        "消息",
        "通知",
        "提醒我",
        "微信",
    ]
    description: str = (
        "通过微信创建定时消息/提醒, 在指定时间发送.\n\n"
        "参数:\n"
        "- message: 消息内容(必填)\n"
        "- send_time: 发送时间(必填), ISO 8601格式, 应携带时区偏移(如 +08:00 / Z), 不带时区按用户本地时区解释\n"
        "- description: 备注(可选)\n\n"
        "注意:\n"
        "- 最多可预约7天内的消息\n"
        "- 同时待发送消息不超过50条\n"
        "- send_time 填过去/当前时间会自动顺延为最近可发送时间\n\n"
        "示例:\n"
        '- 用户: "明天早上8点提醒我吃药" → {"message": "该吃药啦", "send_time": "2026-07-23T08:00:00+08:00", "description": "提醒吃药"}'
    )
    args_schema: type[ScheduleMessageWechatRequest] = ScheduleMessageWechatRequest

    def _get_helper(self) -> ScheduledMessageHelper:
        if not hasattr(self, "_messenger_helper"):
            helper = ScheduledMessageHelper(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_messenger_helper", helper)
        return self._messenger_helper

    async def is_available(self) -> bool:
        helper = self._get_helper()
        channels = await helper.check_channels()
        return "wechat" in channels

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = ScheduleMessageWechatRequest(**kwargs)
            helper = self._get_helper()

            try:
                send_time = datetime.fromisoformat(request.send_time)
            except (ValueError, TypeError) as e:
                return (
                    f"错误: send_time格式无效, 请使用ISO 8601格式并携带时区偏移"
                    f" (如 2026-05-30T08:00:00+08:00): {e}"
                )

            service = await helper.get_service()
            msg = await service.schedule_message(
                message=request.message,
                send_time=send_time,
                description=request.description,
                channel="wechat",
                timezone=helper.get_timezone(),
            )

            local_send_time = (
                msg.send_time
                .replace(tzinfo=UTC)
                .astimezone(ZoneInfo(helper.get_timezone()))
                .strftime("%Y-%m-%d %H:%M")
            )
            return (
                f"✅ 定时消息已创建\n"
                f"- 消息ID: {msg.message_id}\n"
                f"- 发送时间: {local_send_time}\n"
                f"- 渠道: wechat\n"
                f"- 消息内容: {request.message[:100]}"
                + (f"...\n- 备注: {request.description}" if request.description else "")
            )

        except Exception as e:
            logger.error("创建微信定时消息失败: %s", e)
            return format_tool_error(e)


__all__ = ["ScheduleMessageWechatTool"]
