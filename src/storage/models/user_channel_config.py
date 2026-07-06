"""用户消息渠道配置数据模型.

存储每个用户的消息渠道配置, 如OpenClaw会话信息等.
支持多渠道: 微信(通过OpenClaw), 邮件(通过SMTP)等.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator
from sqlmodel import Column, DateTime, SQLModel, text
from sqlmodel import Field as SQLField


class UserChannelConfigBase(SQLModel):
    """用户渠道配置基础模型."""

    user_id: str = Field(..., description="用户ID")
    channel_type: str = Field(..., description="渠道类型: wechat / email")
    is_default: bool = Field(default=False, description="是否为该用户的默认渠道")
    config: str = Field(..., description="渠道配置JSON")

    model_config = {"validate_assignment": True, "str_strip_whitespace": True}

    @field_validator("config")
    @classmethod
    def validate_config(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("渠道配置不能为空")
        import json

        try:
            json.loads(v)
        except json.JSONDecodeError as e:
            raise ValueError(f"渠道配置必须是有效JSON: {e}") from e
        return v.strip()

    def get_config_dict(self) -> dict[str, Any]:
        import json

        return json.loads(self.config)


class UserChannelConfig(UserChannelConfigBase, table=True):
    """用户渠道配置数据表模型."""

    __tablename__ = "user_channel_configs"
    __table_args__ = {"extend_existing": True}

    id: int | None = SQLField(default=None, primary_key=True, description="记录ID")
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
            "user_id": self.user_id,
            "channel_type": self.channel_type,
            "is_default": self.is_default,
            "config": self.config,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


__all__ = [
    "UserChannelConfig",
    "UserChannelConfigBase",
]
