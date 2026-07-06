"""核心通用数据类型定义."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ImageUrl(BaseModel):
    """OpenAI标准图片URL对象."""

    url: str = Field(..., description="Base64编码的图片数据或HTTP URL")


class ContentBlock(BaseModel):
    """OpenAI标准内容块 - 支持文本和图片."""

    type: Literal["text", "image_url"] = Field(..., description="内容类型")
    text: str | None = Field(None, description="文本内容")
    image_url: ImageUrl | None = Field(None, description="图片URL对象")


MessageContent = str | list[ContentBlock]


class ConversationIndexResult(BaseModel):
    """对话索引分析结果."""

    summary: str = Field(description="对话核心总结,最多40个token")
    topic: str = Field(description="主要话题,3-5个词")


class MemoryOperation(BaseModel):
    """置顶记忆单条操作."""

    action: str = Field(description="操作类型: add | delete | change")
    field: str = Field(description="目标字段: basic_info | preferences")
    content: str = Field(
        default="",
        description="add: 新条目内容; delete: 待删除的原文(精确匹配)",
    )
    old_content: str = Field(
        default="",
        description="change: 待替换的原文(精确匹配)",
    )
    new_content: str = Field(
        default="",
        description="change: 替换后的完整内容",
    )


class PinnedMemoryUpdateResult(BaseModel):
    """置顶记忆更新分析结果."""

    has_operations: bool = Field(default=False, description="是否有操作")
    operations: list[MemoryOperation] = Field(
        default_factory=list,
        description="操作列表",
    )


__all__ = [
    "ContentBlock",
    "ConversationIndexResult",
    "ImageUrl",
    "MemoryOperation",
    "MessageContent",
    "PinnedMemoryUpdateResult",
]
