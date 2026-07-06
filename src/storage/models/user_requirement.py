"""用户要求记事本数据模型.

独立存储用户对助手的非一次性要求, 由主对话模型通过 requirement_memory 工具
全文重写维护. 与置顶记忆分库 (独立 requirement_memory.db), 便于排除/删库调试.

设计:
- 每 user/thread 单行, content 为完整要求列表 (一行一条)
- 限额 (≤10 行 / ≤500 字) 由 UserRequirementService 在写入前校验
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import field_validator
from sqlalchemy import Column, DateTime, text
from sqlmodel import Field, Index, SQLModel

logger = logging.getLogger(__name__)


class UserRequirementBase(SQLModel):
    """用户要求记事本基础模型."""

    content: str = Field(default="", max_length=1000, description="要求列表(一行一条)")

    model_config = {"validate_assignment": True, "str_strip_whitespace": True}

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        """空内容合法 (代表清空), 仅做空白裁剪."""
        if not v:
            return ""
        return v.strip()


class UserRequirement(UserRequirementBase, table=True):
    """用户要求记事本数据表 (每 user/thread 单行)."""

    __tablename__ = "user_requirement"
    __table_args__ = (
        Index("idx_user_thread", "user_id", "thread_id", unique=True),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True, description="主键ID")
    user_id: str = Field(..., max_length=100, description="用户ID")
    thread_id: str = Field(..., max_length=100, description="线程ID")

    created_at: datetime | None = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )
    updated_at: datetime | None = Field(
        default_factory=lambda: datetime.now(UTC),
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


__all__ = ["UserRequirement"]
