"""创建定时消息工具 - schedule_message."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, ClassVar, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.scheduled_messenger_base import ScheduledMessengerBase

logger = logging.getLogger(__name__)


class ScheduleMessageRequest(BaseModel):
    """创建定时消息请求."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    message: str = Field(..., description="消息内容, 最长2000字符")
    send_time: str = Field(
        ...,
        description="发送时间, ISO 8601格式, 如 2026-05-30T08:00:00",
    )
    description: str | None = Field(
        None,
        description="备注说明, 如 '提醒用户吃药'",
    )
    channel: str | None = Field(
        None,
        description="发送渠道, 默认使用用户默认渠道: wechat / email",
    )
    subject: str | None = Field(
        None,
        description="邮件主题 (仅channel=email时必填)",
    )
    html_body: str | None = Field(
        None,
        description="邮件HTML正文 (仅email渠道可选, 不提供则使用message纯文本)",
    )
    email_address: str | None = Field(
        None,
        description=(
            "收件邮箱地址 (仅email渠道). "
            "首次使用邮件渠道时必填, 系统自动保存为默认收件地址, 后续可不提供"
        ),
    )


class ScheduleMessageTool(ScheduledMessengerBase):
    """创建定时消息/提醒, 在指定时间通过微信或邮件发送."""

    name: str = "schedule_message"
    search_keywords: ClassVar[list[str]] = [
        "定时",
        "提醒",
        "消息",
        "通知",
        "提醒我",
        "发送",
    ]
    description: str = ""
    args_schema: type[ScheduleMessageRequest] = ScheduleMessageRequest

    @override
    def _apply_description(self, *, has_wechat: bool, has_email: bool) -> None:
        if has_wechat and has_email:
            self.description = """创建定时消息/提醒, 在指定时间通过微信或邮件发送.

参数:
- message: 消息内容(必填)
- send_time: 发送时间(必填), ISO 8601格式, 如 2026-05-30T08:00:00
- channel: 发送渠道, 默认微信. 邮件需显式指定 channel=email
- subject: 邮件主题(channel=email时必填)
- email_address: 收件邮箱(首次邮件必填, 后续自动使用已保存地址)
- description: 备注(可选)

注意:
- 最多可预约7天内的消息
- 同时待发送消息不超过50条
- send_time 填过去/当前时间会自动顺延为最近可发送时间"""
        elif has_email:
            self.description = """创建定时消息/提醒, 在指定时间通过邮件发送.

参数:
- message: 消息内容(必填)
- send_time: 发送时间(必填), ISO 8601格式
- subject: 邮件主题(必填)
- email_address: 收件邮箱(首次必填, 系统自动保存, 后续可不提供)
- description: 备注(可选)

注意:
- 最多可预约7天内的消息, 同时待发送不超过50条
- send_time 填过去/当前时间会自动顺延为最近可发送时间"""
        else:
            self.description = """创建定时消息/提醒, 在指定时间通过微信发送.

参数:
- message: 消息内容(必填)
- send_time: 发送时间(必填), ISO 8601格式
- description: 备注(可选)

注意:
- 最多可预约7天内的消息, 同时待发送不超过50条
- send_time 填过去/当前时间会自动顺延为最近可发送时间"""

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = ScheduleMessageRequest(**kwargs)

            effective_channel = request.channel or self._resolve_default_channel()
            if effective_channel == "email" and not request.subject:
                return "错误: email渠道需要提供subject参数(邮件主题)"

            if effective_channel == "email":
                email_error = await self._resolve_email_address(request.email_address)
                if email_error:
                    return email_error

            try:
                send_time = datetime.fromisoformat(request.send_time)
            except (ValueError, TypeError) as e:
                return (
                    f"错误: send_time格式无效, 请使用ISO 8601格式"
                    f" (如 2026-05-30T08:00:00): {e}"
                )

            service = await self._get_service()
            msg = await service.schedule_message(
                message=request.message,
                send_time=send_time,
                description=request.description,
                channel=effective_channel,
                subject=request.subject,
                html_body=request.html_body,
                timezone=self._get_timezone(),
            )

            # msg.send_time 是 naive UTC, 转换为本地时间展示
            local_send_time = (
                msg.send_time
                .replace(tzinfo=UTC)
                .astimezone(ZoneInfo(self._get_timezone()))
                .strftime("%Y-%m-%d %H:%M")
            )
            channel_info = f"\n- 渠道: {msg.channel}" if msg.channel else ""
            return (
                f"✅ 定时消息已创建\n"
                f"- 消息ID: {msg.message_id}\n"
                f"- 发送时间: {local_send_time}"
                f"{channel_info}\n"
                f"- 消息内容: {request.message[:100]}"
                + (f"...\n- 备注: {request.description}" if request.description else "")
            )

        except Exception as e:
            logger.error("创建定时消息失败: %s", e)
            return self._format_error(e)


__all__ = ["ScheduleMessageTool"]
