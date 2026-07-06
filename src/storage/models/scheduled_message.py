"""定时消息数据模型定义."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator
from sqlmodel import Column, DateTime, SQLModel, text
from sqlmodel import Field as SQLField


class MessageStatus(StrEnum):
    """定时消息状态枚举."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"
    MISSED = "missed"
    NOTIFIED = "notified"


class ScheduledMessageBase(SQLModel):
    """定时消息基础模型."""

    message_id: str = Field(
        default_factory=lambda: f"msg_{uuid.uuid4().hex[:8]}",
        description="消息唯一标识",
    )
    message: str = Field(..., description="消息内容")
    send_time: datetime = Field(..., description="计划发送时间")
    status: MessageStatus = Field(default=MessageStatus.PENDING, description="消息状态")
    description: str | None = Field(None, description="备注说明")
    sent_at: datetime | None = Field(None, description="实际发送时间")
    channel: str = Field(default="wechat", description="消息发送渠道: wechat / email")
    subject: str | None = Field(None, description="邮件主题 (仅email渠道使用)")
    html_body: str | None = Field(
        None,
        description="邮件HTML正文 (仅email渠道, 不提供则使用message作为纯文本)",
    )

    model_config = {"validate_assignment": True, "str_strip_whitespace": True}

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("消息内容不能为空")
        if len(v) > 2000:
            raise ValueError("消息内容不能超过2000个字符")
        return v.strip()


class ScheduledMessage(ScheduledMessageBase, table=True):
    """定时消息数据表模型."""

    __tablename__ = "scheduled_messages"
    __table_args__ = {"extend_existing": True}

    id: int | None = SQLField(default=None, primary_key=True, description="记录ID")
    user_id: str = Field(..., description="用户ID")
    thread_id: str = Field(..., description="线程ID")
    agent_id: str = Field(..., description="Agent ID")
    created_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )
    updated_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=text("CURRENT_TIMESTAMP"),
        ),
        description="更新时间",
    )

    class Config:
        from_attributes = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "message": self.message,
            "send_time": self.send_time.isoformat() if self.send_time else None,
            "status": self.status,
            "description": self.description,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "channel": self.channel,
            "subject": self.subject,
            "html_body": self.html_body,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "agent_id": self.agent_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


__all__ = [
    "MessageStatus",
    "ScheduledMessage",
    "ScheduledMessageBase",
]
