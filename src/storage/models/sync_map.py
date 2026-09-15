"""Graph 同步映射表模型.

用户级存储 (data/{user_id}/database/graph_sync.db), 记录本地 todo/日历条目
与 Graph 远端对象的对应关系. 远端 id 是超长 base64, 只落本表由代码消费.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, DateTime, text
from sqlmodel import Field, SQLModel, UniqueConstraint
from sqlmodel import Field as SQLField


class SyncItemKind(StrEnum):
    """同步对象类型."""

    TODO = "todo"
    EVENT = "event"


class SyncMap(SQLModel, table=True):
    """本地条目 <-> Graph 远端对象映射."""

    __tablename__ = "graph_sync_map"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "local_id", name="uq_sync_local"),
        {"extend_existing": True},
    )

    id: int | None = SQLField(default=None, primary_key=True, description="映射ID")
    user_id: str = Field(..., index=True, description="用户ID")
    kind: SyncItemKind = Field(..., description="对象类型 todo/event")
    local_id: int = Field(
        ..., description="本地条目ID (todo_items/calendar_events 主键)"
    )
    remote_id: str = Field(..., description="Graph 对象ID (超长 base64, 勿手抄)")
    content_hash: str | None = Field(
        None,
        max_length=64,
        description="上次同步成功时的内容摘要 (变更检测)",
    )
    last_synced_at: datetime | None = Field(
        None,
        description="上次成功同步时间",
    )
    created_at: datetime | None = Field(
        default_factory=lambda: None,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
    )
    updated_at: datetime | None = Field(
        default_factory=lambda: None,
        sa_column=Column(
            DateTime,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=text("CURRENT_TIMESTAMP"),
        ),
    )


__all__ = ["SyncItemKind", "SyncMap"]
