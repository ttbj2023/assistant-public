"""模型类型和能力枚举定义."""

from __future__ import annotations

from enum import StrEnum


class ModelType(StrEnum):
    """模型类型枚举."""

    EMBEDDING = "embedding"
    CHAT = "chat"
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"


class ModelCapability(StrEnum):
    """模型能力枚举."""

    # 输入能力
    TEXT_INPUT = "text_input"
    IMAGE_INPUT = "image_input"
    AUDIO_INPUT = "audio_input"
    VIDEO_INPUT = "video_input"

    # 推理能力
    REASONING = "reasoning"

    # 功能能力
    TOOL_CALLING = "tool_calling"
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"

    # 输出能力
    STREAMING = "streaming"
    JSON_MODE = "json_mode"


# 模型类型描述
MODEL_TYPE_DESCRIPTIONS = {
    ModelType.EMBEDDING: "嵌入模型",
    ModelType.CHAT: "对话模型",
    ModelType.IMAGE_GENERATION: "图片生成模型",
    ModelType.VIDEO_GENERATION: "视频生成模型",
}
