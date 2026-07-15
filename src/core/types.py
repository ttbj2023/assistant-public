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


__all__ = [
    "ContentBlock",
    "ConversationIndexResult",
    "ImageUrl",
    "MessageContent",
]
