"""日历订阅 token 数据模型.

用户级存储 (calendar.db 内), 持久 token 无过期机制 --
HMAC 过期会静默断流手机订阅, 改为显式撤销语义 (revoked_at).
只存 sha256 哈希, 明文 token 仅在创建时返回一次.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, field_validator
from sqlalchemy import Column, DateTime, text
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel

from src.core.datetime_utils import now_utc


class CalendarSubscriptionBase(SQLModel):
    """日历订阅基础模型."""

    user_id: str = Field(..., description="用户ID")
    token_hash: str = Field(
        ...,
        max_length=64,
        description="token 的 sha256 hex (明文不落库)",
    )
    subscription_id: str = Field(
        default_factory=lambda: f"sub_{uuid.uuid4().hex[:8]}",
        max_length=32,
        description="订阅标识 (展示用, 非 secret)",
    )

    model_config = {"validate_assignment": True, "str_strip_whitespace": True}

    @field_validator("token_hash")
    @classmethod
    def validate_token_hash(cls, v: str) -> str:
        """验证 token 哈希格式."""
        if len(v) != 64:
            raise ValueError("token_hash 必须为 64 位 sha256 hex")
        return v.lower()


class CalendarSubscription(CalendarSubscriptionBase, table=True):
    """日历订阅 token 记录."""

    __tablename__ = "calendar_subscriptions"
    __table_args__ = {"extend_existing": True}

    id: int | None = SQLField(default=None, primary_key=True, description="订阅ID")
    created_at: datetime | None = Field(
        default_factory=now_utc,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )
    last_access_at: datetime | None = Field(
        None,
        sa_column=Column(DateTime),
        description="最近一次 ICS 拉取时间",
    )
    revoked_at: datetime | None = Field(
        None,
        sa_column=Column(DateTime),
        description="撤销时间, 非 None 即失效",
    )

    class Config:
        """SQLModel配置."""

        from_attributes = True

    def to_dict(self) -> dict[str, str | None]:
        """转换为字典格式 (不含 token 哈希等敏感字段)."""
        return {
            "subscription_id": self.subscription_id,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_access_at": (
                self.last_access_at.isoformat() if self.last_access_at else None
            ),
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
        }


__all__ = ["CalendarSubscription", "CalendarSubscriptionBase"]
