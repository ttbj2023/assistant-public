"""简化的2字段置顶记忆数据模型.

基于业务需求重新设计的极简化置顶记忆模型,直接按照存储层规范实现.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import field_validator
from sqlalchemy import Column, DateTime, text
from sqlmodel import Field, Index, SQLModel

logger = logging.getLogger(__name__)


class SimplePinnedMemoryType(StrEnum):
    """简化置顶记忆类型枚举 - 精准对齐业务需求.

    字段复用说明: PREFERENCES 被 local 模式(口味偏好)和 simple 模式(领域输出偏好)
    共用, 语义由各 Agent 的提取 prompt 定义; 物理隔离(独立 DB 文件)使二者不冲突.
    """

    BASIC_INFO = "basic_info"  # 用户基本画像(客观事实, local 模式专用)
    PREFERENCES = (
        "preferences"  # 用户稳定偏好(local=口味偏好; simple=领域输出偏好/要求)
    )
    INSIGHTS = "insights"  # 可复用经验/模式/领域知识(simple 模式专用)


class SimplePinnedMemoryBase(SQLModel):
    """简化置顶记忆基础模型."""

    memory_type: SimplePinnedMemoryType = Field(
        ...,
        description="记忆类型(2字段之一)",
    )
    content: str = Field(..., max_length=5000, description="记忆内容")
    priority: int = Field(default=50, description="优先级(0-100)")

    # 业务验证配置
    model_config = {"validate_assignment": True, "str_strip_whitespace": True}

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        """验证记忆内容."""
        # 允许空内容,因为某些记忆类型可能暂时没有内容
        if not v or not v.strip():
            return ""  # 返回空字符串,而不是抛出异常
        if len(v.strip()) > 5000:  # 增大内容限制
            raise ValueError("记忆内容过长,最多5000个字符")
        return v.strip()

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: int) -> int:
        """验证优先级."""
        if v < 0 or v > 100:
            raise ValueError("优先级必须在0-100之间")
        return v


class SimplePinnedMemory(SimplePinnedMemoryBase, table=True):
    """简化置顶记忆数据表模型."""

    __tablename__ = "simple_pinned_memory"
    __table_args__ = (
        Index("idx_user_thread_type", "user_id", "thread_id", "memory_type"),
        Index("idx_user_priority", "user_id", "priority"),
        Index("idx_updated_at", "updated_at"),
        {"extend_existing": True},
    )

    # 主键和用户隔离字段
    id: int | None = Field(default=None, primary_key=True, description="主键ID")
    user_id: str = Field(..., max_length=100, description="用户ID")
    thread_id: str = Field(..., max_length=100, description="线程ID")

    # 自动时间戳
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
    access_count: int = Field(default=0, description="访问次数")

    class Config:
        """SQLModel配置."""

        from_attributes = True


__all__ = [
    "SimplePinnedMemory",
    "SimplePinnedMemoryType",
]
