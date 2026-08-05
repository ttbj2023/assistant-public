"""定时消息(邮件渠道) - schedule_message_email."""

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


class ScheduleMessageEmailRequest(BaseModel):
    """邮件定时消息请求."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    message: str = Field(..., description="消息内容(邮件正文), 最长2000字符")
    send_time: str = Field(
        ...,
        description=(
            "发送时间, ISO 8601格式且应携带时区偏移, "
            "如 2026-05-30T08:00:00+08:00 或 2026-05-30T00:00:00Z. "
            "不带时区时按用户本地时区解释"
        ),
    )
    subject: str = Field(..., description="邮件主题(必填)")
    email_address: str | None = Field(
        None,
        description=(
            "收件邮箱地址. "
            "首次使用邮件渠道时必填, 系统自动保存为默认收件地址, 后续可不提供"
        ),
    )
    html_body: str | None = Field(
        None,
        description="邮件HTML正文(可选, 不提供则使用message纯文本)",
    )
    description: str | None = Field(
        None,
        description="备注说明, 如 '提醒用户审批'",
    )


@sync_runnable
class ScheduleMessageEmailTool(BaseTool):
    """通过邮件创建定时消息/提醒."""

    name: str = "schedule_message_email"
    search_keywords: ClassVar[list[str]] = [
        "定时",
        "提醒",
        "消息",
        "通知",
        "提醒我",
        "邮件",
        "email",
    ]
    description: str = (
        "通过邮件创建定时消息/提醒, 在指定时间发送.\n\n"
        "参数:\n"
        "- message: 消息内容/邮件正文(必填)\n"
        "- send_time: 发送时间(必填), ISO 8601格式, 应携带时区偏移(如 +08:00 / Z), 不带时区按用户本地时区解释\n"
        "- subject: 邮件主题(必填)\n"
        "- email_address: 收件邮箱(首次必填, 系统自动保存, 后续可不提供)\n"
        "- html_body: HTML正文(可选)\n"
        "- description: 备注(可选)\n\n"
        "注意:\n"
        "- 最多可预约7天内的消息\n"
        "- 同时待发送消息不超过50条\n"
        "- send_time 填过去/当前时间会自动顺延为最近可发送时间\n\n"
        "示例:\n"
        '- 用户: "明天下午3点发邮件提醒老板审批" → {"message": "请尽快审批...", "send_time": "2026-07-23T15:00:00+08:00", "subject": "审批提醒"}'
    )
    args_schema: type[ScheduleMessageEmailRequest] = ScheduleMessageEmailRequest

    def _get_helper(self) -> ScheduledMessageHelper:
        if not hasattr(self, "_messenger_helper"):
            helper = ScheduledMessageHelper(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_messenger_helper", helper)
        return self._messenger_helper

    async def is_available(self) -> bool:
        return self._get_helper().check_smtp_config()

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = ScheduleMessageEmailRequest(**kwargs)
            helper = self._get_helper()

            email_error = await helper.resolve_email_address(request.email_address)
            if email_error:
                return email_error

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
                channel="email",
                subject=request.subject,
                html_body=request.html_body,
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
                f"- 渠道: email\n"
                f"- 主题: {request.subject}\n"
                f"- 消息内容: {request.message[:100]}"
                + (f"...\n- 备注: {request.description}" if request.description else "")
            )

        except Exception as e:
            logger.error("创建邮件定时消息失败: %s", e)
            return format_tool_error(e)


__all__ = ["ScheduleMessageEmailTool"]
