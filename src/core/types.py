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


UsageUnitType = Literal["token", "count"]
UsageAccuracy = Literal["exact", "estimated", "unknown"]


class UsageRecordCreate(BaseModel):
    """创建用量记录的输入模型 - inference 产出 / storage 消费的跨层契约."""

    user_id: str
    thread_id: str
    agent_id: str
    round_number: int | None = None
    request_id: str | None = None

    operation: str
    usage_source: str
    provider: str | None = None
    model_id: str | None = None
    run_id: str | None = None
    parent_run_id: str | None = None
    external_job_id: str | None = None

    unit_type: UsageUnitType = "token"
    request_count: int = 1
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    reasoning_tokens: int | None = None

    accuracy: UsageAccuracy = "unknown"
    success: bool = True
    duration_ms: int | None = None
    raw_usage: dict | None = Field(default=None)
    metadata: dict | None = Field(default=None)


__all__ = [
    "ContentBlock",
    "ConversationIndexResult",
    "ImageUrl",
    "MessageContent",
    "UsageAccuracy",
    "UsageRecordCreate",
    "UsageUnitType",
]
