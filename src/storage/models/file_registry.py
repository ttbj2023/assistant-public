"""用户级文件注册表数据模型.

合并附件注册表与文件去重索引为单一用户级 SSOT, 消除跨库清理协同问题.

存储于用户级数据库 file_registry.db (data/{user_id}/database/file_registry.db).
每个 file_id 一条记录, 保留独立元数据 (brief/desc_path/round_number 等);
通过 content_hash 关联去重, 引用计数由实时查询实现, 无需维护 reference_count 字段.

清理时以本表为权威, 单库事务保证原子性:
    删一条记录 → 删对应描述文件 → 按 content_hash 实时统计引用 → 归零则删物理文件.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, text
from sqlmodel import Field, SQLModel


class FileEntry(SQLModel, table=True):
    """用户级文件注册表记录 (替代旧 AttachmentRegistryEntry + FileHashIndex)."""

    __tablename__ = "file_registry"
    __table_args__ = {"extend_existing": True}

    file_id: str = Field(
        ...,
        primary_key=True,
        max_length=12,
        description="文件唯一标识 (8位hex)",
    )
    file_type: str = Field(..., description="文件类型: image / document")
    physical_path: str = Field(
        ...,
        description="主文件相对路径 (相对于 user_base_path)",
    )
    desc_path: str | None = Field(
        default=None,
        description="描述文件 (.desc.md) 相对路径, 与主文件一一对应, 清理同生共死",
    )
    filename: str = Field(..., description="文件名")
    brief: str = Field(
        default="",
        description="概要描述, 用于 [file: id] 标记",
    )
    file_format: str | None = Field(
        default=None,
        description="文件格式: jpg/docx/pdf",
    )
    file_size: int | None = Field(
        default=None,
        description="文件大小(字节)",
    )
    content_hash: str | None = Field(
        default=None,
        max_length=64,
        index=True,
        description="文件内容 SHA-256 哈希, 去重键 (建索引加速去重查询)",
    )
    round_number: int = Field(..., description="产生该文件的对话轮次")
    owner_thread_id: str = Field(
        ...,
        description="首次产生的线程ID (溯源, 物理文件虽用户级共享但元数据记录产生源)",
    )
    owner_agent_id: str = Field(
        ...,
        description="首次产生的 Agent ID (溯源)",
    )
    created_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime,
            server_default=text("CURRENT_TIMESTAMP"),
        ),
        description="创建时间",
    )
    document_meta: str | None = Field(
        default=None,
        description="文档结构化元数据JSON (摘要+目录信息), 仅document类型",
    )

    class Config:
        from_attributes = True


__all__ = ["FileEntry"]
