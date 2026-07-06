"""流式响应相关类型定义和工具函数.

提供OpenAI兼容的流式响应支持,包括SSE格式化和相关类型定义.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, Field


@dataclass
class StreamContent:
    """流式内容包装器, 区分展示内容和记忆内容."""

    content: str
    display_only: bool = False


class StreamChunk(BaseModel):
    """流式响应数据块 - OpenAI兼容格式."""

    id: str
    object: str = Field(default="chat.completion.chunk", description="对象类型")
    created: int = Field(description="创建时间戳")
    model: str = Field(description="模型名称")
    choices: list[dict[str, Any]] = Field(description="选择列表")
    index: int = Field(default=0, description="选择索引")

    class Config:
        json_encoders: ClassVar[dict[type, Any]] = {int: lambda v: v}


def format_sse_chunk(chunk: StreamChunk) -> str:
    """将数据块格式化为SSE格式."""
    return f"data: {chunk.model_dump_json()}\n\n"


def create_stream_chunk(
    completion_id: str,
    created: int,
    model: str,
    content: str | None = None,
    finish_reason: str | None = None,
) -> str:
    """创建流式响应数据块."""
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content

    choices = [
        {
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        },
    ]

    chunk = StreamChunk(id=completion_id, created=created, model=model, choices=choices)
    return format_sse_chunk(chunk)


def create_stream_final_chunk(completion_id: str, created: int, model: str) -> str:
    """创建流式响应的结束标记."""
    final_chunk = create_stream_chunk(
        completion_id=completion_id,
        created=created,
        model=model,
        finish_reason="stop",
    )
    done_marker = "data: [DONE]\n\n"
    return final_chunk + done_marker


def create_stream_error_chunk(
    error_message: str,
    error_type: str = "stream_error",
) -> str:
    """创建流式响应的错误数据块."""
    error_data = {
        "error": {
            "message": error_message,
            "type": error_type,
            "code": "stream_processing_error",
        },
    }
    return f"data: {json.dumps(error_data)}\n\n"


def generate_completion_id() -> str:
    """生成完成ID."""
    return f"chatcmpl-{int(time.time() * 1000)}"


__all__ = [
    "StreamChunk",
    "StreamContent",
    "create_stream_chunk",
    "create_stream_error_chunk",
    "create_stream_final_chunk",
    "format_sse_chunk",
    "generate_completion_id",
]
