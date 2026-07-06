"""TODO相关的数据模型定义.

包含TODO任务的所有SQLModel数据模型定义,用于数据库持久化和业务逻辑验证.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator
from sqlmodel import Column, DateTime, SQLModel, text
from sqlmodel import Field as SQLField


class TodoPriority(StrEnum):
    """TODO任务优先级枚举."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TodoStatus(StrEnum):
    """TODO任务状态枚举."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TodoItemBase(SQLModel):
    """TODO任务基础模型."""

    title: str = Field(..., description="任务标题")
    description: str | None = Field(None, description="任务描述")
    priority: TodoPriority = Field(
        default=TodoPriority.MEDIUM,
        description="任务优先级",
    )
    status: TodoStatus = Field(default=TodoStatus.PENDING, description="任务状态")
    tags: str | None = Field(None, description="任务标签,逗号分隔")
    due_date: datetime | None = Field(None, description="截止日期")

    model_config = {"validate_assignment": True, "str_strip_whitespace": True}

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        """验证任务标题."""
        if not v or not v.strip():
            raise ValueError("任务标题不能为空")
        if len(v) > 200:
            raise ValueError("任务标题不能超过200个字符")
        return v.strip()

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: str | None) -> str | None:
        """验证任务标签."""
        if v is None:
            return None
        # 按照测试期望完全清理空格:分割,清理,过滤,重新组合
        tags = [tag.strip() for tag in v.split(",") if tag.strip()]
        if len(tags) > 10:
            raise ValueError("任务标签不能超过10个")
        return ",".join(tags)


class TodoItem(TodoItemBase, table=True):
    """TODO任务数据表模型."""

    __tablename__ = "todo_items"
    __table_args__ = {"extend_existing": True}

    id: int | None = SQLField(default=None, primary_key=True, description="任务ID")
    user_id: str = Field(..., description="用户ID")
    thread_id: str = Field(..., description="线程ID")
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
        """SQLModel配置."""

        from_attributes = True

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式.

        Returns:
            包含所有字段的字典

        """
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority.value,
            "status": self.status.value,
            "tags": self.tags,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


__all__ = [
    "TodoItem",
    "TodoItemBase",
    "TodoPriority",
    "TodoStatus",
]
