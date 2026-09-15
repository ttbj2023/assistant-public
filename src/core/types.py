"""核心通用数据类型定义."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ImageUrl(BaseModel):
    """OpenAI标准图片URL对象."""

    url: str = Field(..., description="Base64编码的图片数据或HTTP URL")


class FileData(BaseModel):
    """文档内容对象 — 纯文本或二进制 (docx/pdf/pptx, 经 doc2md 解析).

    content 与 content_b64 二选一: 前者纯文本直存, 后者 base64 二进制
    走 doc2md 转换管线.
    """

    filename: str = Field(..., description="原始文件名 (含扩展名)")
    content: str | None = Field(None, description="纯文本内容 (md/txt 等)")
    content_b64: str | None = Field(None, description="二进制内容 base64 (docx/pdf 等)")

    @model_validator(mode="after")
    def _require_content(self) -> FileData:
        """content / content_b64 必须提供其一."""
        if self.content is None and self.content_b64 is None:
            raise ValueError("file 内容块必须提供 content 或 content_b64 之一")
        return self


class ContentBlock(BaseModel):
    """OpenAI标准内容块 - 支持文本/图片/纯文本文档."""

    type: Literal["text", "image_url", "file"] = Field(..., description="内容类型")
    text: str | None = Field(None, description="文本内容")
    image_url: ImageUrl | None = Field(None, description="图片URL对象")
    file: FileData | None = Field(None, description="纯文本文档对象")


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
    "FileData",
    "ImageUrl",
    "MessageContent",
    "UsageAccuracy",
    "UsageRecordCreate",
    "UsageUnitType",
]
