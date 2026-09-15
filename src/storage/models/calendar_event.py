"""日历事件数据模型.

用户级存储 (data/{user_id}/database/calendar.db), 跨线程统一视图.
时间为 aware UTC (遵循 src.core.datetime_utils 约定);
all_day 事件的 end_time 语义为 inclusive 结束日 (生成 ICS 时转 exclusive).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator
from sqlalchemy import Column, DateTime, text
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel

from src.core.datetime_utils import now_utc


class CalendarEventStatus(StrEnum):
    """日程状态枚举."""

    ACTIVE = "active"
    CANCELLED = "cancelled"


def _generate_event_uid() -> str:
    """生成日程 UID, ICS 输出时追加域名后缀保持稳定."""
    return f"evt_{uuid.uuid4().hex[:8]}"


class CalendarEventBase(SQLModel):
    """日历事件基础模型."""

    title: str = Field(..., description="日程标题")
    description: str | None = Field(None, description="日程描述")
    location: str | None = Field(None, description="地点")
    start_time: datetime = Field(..., description="开始时间 (aware UTC)")
    end_time: datetime = Field(
        ..., description="结束时间 (aware UTC; 全天事件为 inclusive 结束日)"
    )
    all_day: bool = Field(default=False, description="是否全天事件")
    recurrence_rule: str | None = Field(
        None,
        description="RRULE 字符串 (如 FREQ=WEEKLY;INTERVAL=1), 无则单次事件",
    )
    recurrence_until: datetime | None = Field(
        None, description="重复规则 UNTIL (aware UTC)"
    )
    status: CalendarEventStatus = Field(
        default=CalendarEventStatus.ACTIVE,
        description="日程状态",
    )

    model_config = {"validate_assignment": True, "str_strip_whitespace": True}

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        """验证日程标题."""
        if not v or not v.strip():
            raise ValueError("日程标题不能为空")
        if len(v) > 200:
            raise ValueError("日程标题不能超过200个字符")
        return v.strip()

    # mode="before": SQLModel 表模型的 after 校验器在字段赋值前触发, 只能在原始 dict 上校验
    @model_validator(mode="before")
    @classmethod
    def validate_time_range(cls, data: Any) -> Any:
        """验证结束时间不早于开始时间."""
        if isinstance(data, dict):
            start = data.get("start_time")
            end = data.get("end_time")
            if start is not None and end is not None and end < start:
                raise ValueError("结束时间不能早于开始时间")
        return data


class CalendarEvent(CalendarEventBase, table=True):
    """日历事件数据表模型."""

    __tablename__ = "calendar_events"
    __table_args__ = {"extend_existing": True}

    id: int | None = SQLField(default=None, primary_key=True, description="日程ID")
    event_uid: str = Field(
        default_factory=_generate_event_uid,
        max_length=32,
        index=True,
        description="日程唯一标识 (ICS UID 本体)",
    )
    user_id: str = Field(..., description="用户ID")
    source_thread_id: str | None = Field(
        None,
        description="创建线程ID (溯源, 数据用户级共享)",
    )
    source_agent_id: str | None = Field(
        None,
        description="创建 Agent ID (溯源)",
    )
    created_at: datetime | None = Field(
        default_factory=now_utc,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )
    updated_at: datetime | None = Field(
        default_factory=now_utc,
        sa_column=Column(
            DateTime,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=text("CURRENT_TIMESTAMP"),
        ),
        description="更新时间",
    )

    class Config:
        """SQLModel配置."""

        from_attributes = True

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式.

        Returns:
            包含所有字段的字典

        """
        return {
            "id": self.id,
            "event_uid": self.event_uid,
            "title": self.title,
            "description": self.description,
            "location": self.location,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "all_day": self.all_day,
            "recurrence_rule": self.recurrence_rule,
            "recurrence_until": (
                self.recurrence_until.isoformat() if self.recurrence_until else None
            ),
            "status": self.status.value,
            "user_id": self.user_id,
            "source_thread_id": self.source_thread_id,
            "source_agent_id": self.source_agent_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


__all__ = [
    "CalendarEvent",
    "CalendarEventBase",
    "CalendarEventStatus",
]
