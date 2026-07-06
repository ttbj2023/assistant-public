"""文件管理子系统的数据模型.

定义附件传输对象 (AttachmentDTO) — 全项目唯一的附件元数据载体,
用于工具层 / Service 层 / 编排层之间传递附件信息.

设计原则: Entity + DTO 两套模型
- Entity: AttachmentRegistryEntry (src/storage/models/attachment_registry.py, SQLModel 持久化)
- DTO: AttachmentDTO (本模块, Pydantic 内存传递)

字段名与 Entity 完全一致, 通过 model_validate (from_attributes=True) 实现零映射转换.
原 ConversationData 内嵌的 AttachmentInfo (字段名 url/description/id) 已删除,
全项目统一使用本模块的 AttachmentDTO.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


def generate_file_id() -> str:
    """生成 8 位唯一附件 ID.

    Returns:
        8 位 hex 字符串 (uuid4 前 8 位)

    """
    return uuid.uuid4().hex[:8]


class AttachmentDTO(BaseModel):
    """附件传输对象 — 工具层与 Service 层之间的统一附件元数据载体.

    字段名与 AttachmentRegistryEntry (Entity) 完全一致, 通过 from_entity()
    可零映射转换. 替代原 core/attachment_registry.py 的 AttachmentEntry dataclass.
    """

    model_config = ConfigDict(from_attributes=True)

    file_id: str
    file_type: str
    internal_path: str
    filename: str
    brief: str = ""
    file_format: str | None = None
    file_size: int | None = None
    content_hash: str | None = None
    round_number: int = 0
    created_at: datetime | None = None
    document_meta: str | None = None


__all__ = ["AttachmentDTO", "generate_file_id"]
