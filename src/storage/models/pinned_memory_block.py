"""统一置顶记忆单一块数据模型.

主模型每轮全文覆写的存储载体. 每 user/thread/agent 单行, content 为完整
记忆文本 (一行一条). 与 simple_pinned_memory 表共存于 pinned_memory.db
(迁移期间), 迁移完成后旧表停用.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import field_validator
from sqlalchemy import Column, DateTime, text
from sqlmodel import Field, Index, SQLModel

logger = logging.getLogger(__name__)


class PinnedMemoryBlockBase(SQLModel):
    """统一置顶记忆单一块基础模型."""

    content: str = Field(default="", max_length=2000, description="记忆内容(一行一条)")

    model_config = {"validate_assignment": True, "str_strip_whitespace": True}

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        """空内容合法 (代表清空), 仅做空白裁剪."""
        if not v:
            return ""
        return v.strip()


class PinnedMemoryBlock(PinnedMemoryBlockBase, table=True):
    """统一置顶记忆单一块数据表 (每 user/thread 单行)."""

    __tablename__ = "pinned_memory_block"
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


__all__ = ["PinnedMemoryBlock"]
