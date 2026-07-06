"""模型元数据定义.

每个模型定义自己的SDK参数体系, 不做跨模型统一.
model_params 记录该模型SDK原生参数及其默认值, 纯声明式文档.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, override

from .model_types import ModelCapability, ModelType
from .validation import (
    validate_capabilities_consistency,
    validate_model_metadata_basic,
    validate_provider_config,
)


@dataclass(frozen=True)
class ModelPricing:
    """模型厂商官方标准调用价(模型级), 与 provider 实际计费(订阅/转发)无关.

    支持两种计费模式(按模型实际计费方式二选一):
    - 按 token: 填 input/output(每百万 tokens), 可选 cached_input.
    - 按次: 填 per_call(每次调用/每张图/每段视频).
    """

    input: float | None = None  # 标准输入价(每百万 tokens)
    output: float | None = None  # 标准输出价(每百万 tokens)
    cached_input: float | None = (
        None  # 缓存命中价(prompt cache 折扣, 按 token 模式适用)
    )
    currency: str = "CNY"  # CNY(元) | USD($)
    # 按次计费(每次调用/每张图/每段视频), 与 token 计费互斥
    per_call: float | None = None

    @property
    def is_per_call(self) -> bool:
        """是否按次计费(per_call 模式)."""
        return self.per_call is not None


@dataclass
class ModelMetadata:
    """完整的模型元数据定义."""

    # 基础信息
    id: str  # 唯一标识符,如 "deepseek:deepseek-v4-flash"
    name: str  # 用户友好的显示名称
    provider: str  # 模型提供者:local, openai, deepseek, gemini
    model_type: ModelType  # 模型类型
    description: str  # 模型描述

    # 模型SDK参数体系
    # key=SDK原生参数名, value={"default": ..., 可选 "min"/"max"/"options"/"desc"}
    # 每个模型定义自己的完整参数集, 不做跨模型统一
    # agent.yaml中的llm_config参数名必须与此处定义的key一致
    model_params: dict[str, dict]

    # 能力信息 (项目自定义逻辑标识, 与SDK参数无关)
    capabilities: list[ModelCapability]  # 支持的能力列表
    supported_formats: list[str] = field(
        default_factory=list,
    )  # 支持的输入格式, 自动从capabilities推导
    pricing: ModelPricing | None = None  # 厂商官方标准价(模型级)

    def __post_init__(self) -> None:
        """验证模型元数据的完整性."""

        # 验证基本数据完整性
        validate_model_metadata_basic(self)

        # 验证供应商存在性
        with suppress(ValueError):
            validate_provider_config(self.provider)

        # 验证model_params结构
        self._validate_model_params()

        # 验证能力一致性
        self._validate_capabilities_consistency()

    def _validate_model_params(self) -> None:
        """验证model_params的结构完整性."""
        if not isinstance(self.model_params, dict):
            raise ValueError(f"model_params 必须是字典: {self.model_params}")

        for param_name, param_config in self.model_params.items():
            if not isinstance(param_config, dict):
                raise ValueError(f"参数 {param_name} 的配置必须是字典: {param_config}")

            if "default" not in param_config:
                raise ValueError(f"参数 {param_name} 必须包含 default: {param_config}")

            default_val = param_config["default"]

            # default 为 None 表示该参数可选(不传给SDK)
            # dict 类型用于 extra_body 等复合参数
            if default_val is not None and not isinstance(
                default_val,
                (int, float, str, bool, dict),
            ):
                raise ValueError(
                    f"参数 {param_name}.default 必须是基本类型/None/dict: {default_val}",
                )

            # 有 options 时验证 default 在选项中
            if "options" in param_config and (
                default_val is not None and default_val not in param_config["options"]
            ):
                raise ValueError(
                    f"参数 {param_name} 的 default 必须在 options 中: {param_config}",
                )

            # 有 min/max 时验证 default 在范围内
            if (
                "min" in param_config
                and "max" in param_config
                and default_val is not None
                and not (param_config["min"] <= default_val <= param_config["max"])
            ):
                raise ValueError(
                    f"参数 {param_name} 的 default 必须在 min 和 max 之间: {param_config}",
                )

    def _validate_capabilities_consistency(self) -> None:
        """验证能力之间的一致性 - 调用外部验证函数."""
        validate_capabilities_consistency(self.capabilities)

        capabilities_set = set(self.capabilities)

        FORMAT_MAP = {
            ModelCapability.TEXT_INPUT: "text",
            ModelCapability.IMAGE_INPUT: "image",
            ModelCapability.AUDIO_INPUT: "audio",
            ModelCapability.VIDEO_INPUT: "video",
        }
        self.supported_formats = [
            fmt for cap, fmt in FORMAT_MAP.items() if cap in capabilities_set
        ]

    def get_param_defaults(self) -> dict[str, Any]:
        """获取所有非None参数的默认值字典."""
        return {
            k: v["default"]
            for k, v in self.model_params.items()
            if v["default"] is not None
        }

    def get_allowed_param_names(self) -> set[str]:
        """获取该模型允许的参数名集合(白名单)."""
        return set(self.model_params.keys())

    def has_capability(self, capability: ModelCapability) -> bool:
        """检查是否具备特定能力."""
        return capability in self.capabilities

    def is_chat_model(self) -> bool:
        """是否是对话模型."""
        return self.model_type == ModelType.CHAT

    def is_embedding_model(self) -> bool:
        """是否是嵌入模型."""
        return self.model_type == ModelType.EMBEDDING

    def get_json_mode_config(self) -> dict[str, Any]:
        """获取JSON模式的invoke参数配置."""
        if not self.has_capability(ModelCapability.JSON_MODE):
            return {}

        if self.provider == "local":
            return {"format": "json"}

        return {"response_format": {"type": "json_object"}}

    def supports_multimodal(self) -> bool:
        """是否支持多模态输入."""
        return self.has_capability(ModelCapability.IMAGE_INPUT) or self.has_capability(
            ModelCapability.AUDIO_INPUT,
        )

    @override
    def __str__(self) -> str:
        """简洁的字符串表示."""
        return f"{self.name} ({self.id})"

    @override
    def __repr__(self) -> str:
        """详细的字符串表示."""
        return (
            f"ModelMetadata(id={self.id}, name={self.name}, "
            f"type={self.model_type}, capabilities={len(self.capabilities)})"
        )
