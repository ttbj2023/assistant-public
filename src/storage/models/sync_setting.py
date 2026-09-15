"""Graph 同步键值设置模型.

用户级存储 (data/{user_id}/database/graph_sync.db), 保存专用容器 id
(todo_list_id / calendar_id) 等 per-user 同步状态.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, text
from sqlmodel import Field, SQLModel, UniqueConstraint
from sqlmodel import Field as SQLField


class SyncSetting(SQLModel, table=True):
    """per-user 同步键值设置."""

    __tablename__ = "graph_sync_settings"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_sync_setting"),
        {"extend_existing": True},
    )

    id: int | None = SQLField(default=None, primary_key=True, description="设置ID")
    user_id: str = Field(..., index=True, description="用户ID")
    key: str = Field(..., description="设置键 (如 todo_list_id)")
    value: str = Field(..., description="设置值")
    updated_at: datetime | None = Field(
        default_factory=lambda: None,
        sa_column=Column(
            DateTime,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=text("CURRENT_TIMESTAMP"),
        ),
    )


__all__ = ["SyncSetting"]
