"""Provider 无关的共享模型目录.

把跨 provider 重复的底层模型信息独立成一份权威定义,
provider 函数通过 bind_shared() 引用, 不再各自维护.
"""

from __future__ import annotations

from dataclasses import dataclass

from .metadata import ModelMetadata, ModelPricing
from .model_types import ModelCapability, ModelType


@dataclass(frozen=True)
class SharedModel:
    """Provider 无关的模型权威定义."""

    name: str
    model_type: ModelType
    description: str
    model_params: dict[str, dict]
    capabilities: list[ModelCapability]
    default_endpoint_name: str
    pricing: ModelPricing | None = None


def bind_shared(
    provider: str,
    key: str,
    *,
    endpoint_name: str | None = None,
) -> ModelMetadata:
    """把共享模型绑定到某 provider, 生成具体 ModelMetadata.

    caps/params/description/name/pricing 严格取自 SharedModel(权威, 不覆盖).
    仅 endpoint_name 可按网关要求覆盖(id 后缀 = 发给远端的模型名).
    """
    sm = SHARED_MODELS[key]
    ep = endpoint_name or sm.default_endpoint_name
    return ModelMetadata(
        id=f"{provider}:{ep}",
        name=sm.name,
        provider=provider,
        model_type=sm.model_type,
        description=sm.description,
        model_params=sm.model_params,
        capabilities=sm.capabilities,
        pricing=sm.pricing,
    )


# ═══════════════════════════════════════════════════════════════════
# 通用参数模板
# ═══════════════════════════════════════════════════════════════════

_CHAT_CAPS_STANDARD = [
    ModelCapability.TEXT_INPUT,
    ModelCapability.REASONING,
    ModelCapability.STREAMING,
    ModelCapability.JSON_MODE,
    ModelCapability.TOOL_CALLING,
]

_CHAT_CAPS_MULTIMODAL = [
    ModelCapability.TEXT_INPUT,
    ModelCapability.IMAGE_INPUT,
    ModelCapability.REASONING,
    ModelCapability.STREAMING,
    ModelCapability.JSON_MODE,
    ModelCapability.TOOL_CALLING,
]

_KIMI_CHAT_PARAMS = {
    "temperature": {"default": 0.7},
    "top_p": {"default": 0.95},
    "max_tokens": {"default": 32768},
    "stop": {"default": None},
}

_GLM_CHAT_PARAMS = {
    "temperature": {"default": 0.7},
    "top_p": {"default": 0.95},
    "max_tokens": {"default": 32768},
    "stop": {"default": None},
}

_DEEPSEEK_V4_PARAMS = {
    "temperature": {"default": 1.0},
    "top_p": {"default": 1.0},
    "max_tokens": {"default": 32768},
    "reasoning_effort": {"default": None, "options": ["high", "max"]},
    "stop": {"default": None},
}

_QWEN37_CHAT_PARAMS = {
    "temperature": {"default": 0.6},
    "top_p": {"default": 0.95},
    "top_k": {"default": 20},
    "repetition_penalty": {"default": 1.05},
    "max_tokens": {"default": 32768},
    "stop": {"default": None},
    "extra_body": {
        "default": {"enable_thinking": True, "thinking_budget": None},
    },
}

_DOUBAO_SEED_CHAT_PARAMS = {
    "temperature": {"default": 0.3},
    "top_p": {"default": 0.95},
    "max_tokens": {"default": 16384},
    "reasoning_effort": {
        "default": "minimal",
        "options": ["minimal", "low", "medium", "high"],
    },
    "stop": {"default": None},
}

_DOUBAO_SEEDREAM_PARAMS = {
    "size": {"default": "2048x2048"},
    "response_format": {
        "default": "b64_json",
        "options": ["url", "b64_json"],
    },
    "guidance_scale": {"default": None},
    "watermark": {"default": None},
    "seed": {"default": None},
}

_DOUBAO_SEEDANCE_PARAMS = {
    "ratio": {
        "default": "adaptive",
        "options": [
            "16:9",
            "4:3",
            "1:1",
            "3:4",
            "9:16",
            "21:9",
            "adaptive",
        ],
    },
    "duration": {"default": 5},
    "resolution": {
        "default": "720p",
        "options": ["480p", "720p", "1080p"],
    },
    "generate_audio": {"default": True},
    "seed": {"default": None},
    "watermark": {"default": None},
}

_DOUBAO_SEEDANCE_FAST_PARAMS = {
    "ratio": {
        "default": "adaptive",
        "options": [
            "16:9",
            "4:3",
            "1:1",
            "3:4",
            "9:16",
            "21:9",
            "adaptive",
        ],
    },
    "duration": {"default": 5},
    "resolution": {
        "default": "720p",
        "options": ["480p", "720p"],
    },
    "generate_audio": {"default": True},
    "seed": {"default": None},
    "watermark": {"default": None},
}


# ═══════════════════════════════════════════════════════════════════
# 共享模型目录
# ═══════════════════════════════════════════════════════════════════

SHARED_MODELS: dict[str, SharedModel] = {
    "kimi-k2.6": SharedModel(
        name="Kimi K2.6",
        model_type=ModelType.CHAT,
        description="Moonshot Kimi K2.6 推理模型. 支持文本输入、思考推理、工具调用、JSON 模式与流式输出.",
        model_params=_KIMI_CHAT_PARAMS,
        capabilities=_CHAT_CAPS_STANDARD,
        default_endpoint_name="kimi-k2.6",
        # 来源: platform.kimi.ai 官方定价页(元/百万tokens). 262K 上下文窗口.
        pricing=ModelPricing(
            input=6.50, output=27.00, cached_input=1.10, currency="CNY"
        ),
    ),
    "glm-5.2": SharedModel(
        name="GLM 5.2",
        model_type=ModelType.CHAT,
        description="智谱 GLM 5.2 推理模型. 支持文本输入、思考推理、工具调用、JSON 模式与流式输出.",
        model_params=_GLM_CHAT_PARAMS,
        capabilities=_CHAT_CAPS_STANDARD,
        default_endpoint_name="glm-5.2",
        # 来源: 智谱 bigmodel.cn 官方定价(元/百万tokens). 1M 上下文; 缓存命中限时免费(标准价 2 元).
        pricing=ModelPricing(input=8.0, output=28.0, cached_input=2.0, currency="CNY"),
    ),
    "deepseek-v4-pro": SharedModel(
        name="DeepSeek V4 Pro",
        model_type=ModelType.CHAT,
        description="DeepSeek V4 Pro 旗舰云端模型. 1.6T 总参 / 49B 激活 MoE 架构, 1M 上下文, 最大输出 384K(含思考). 支持思考推理、工具调用、JSON 模式与流式输出.",
        model_params=_DEEPSEEK_V4_PARAMS,
        capabilities=_CHAT_CAPS_STANDARD,
        default_endpoint_name="deepseek-v4-pro",
        # 来源: DeepSeek API 官方定价页(api-docs.deepseek.com/quick_start/pricing).
        pricing=ModelPricing(
            input=0.435, output=0.87, cached_input=0.003625, currency="USD"
        ),
    ),
    "qwen3.7-max": SharedModel(
        name="Qwen3.7-Max",
        model_type=ModelType.CHAT,
        description="阿里云 Qwen3.7-Max 旗舰模型. 面向智能体时代, 支持长上下文、思考推理、工具调用、JSON 模式与流式输出.",
        model_params=_QWEN37_CHAT_PARAMS,
        capabilities=_CHAT_CAPS_STANDARD,
        default_endpoint_name="qwen3.7-max",
        # 输入/输出来自 description; 缓存命中按阿里云百炼隐式缓存(命中部分为原价 20%)估算.
        pricing=ModelPricing(input=12.0, output=36.0, cached_input=2.4, currency="CNY"),
    ),
    "doubao-seed-2.0-pro": SharedModel(
        name="Doubao Seed 2.0 Pro",
        model_type=ModelType.CHAT,
        description="字节跳动豆包 Seed 2.0 Pro 旗舰级模型. 支持图像输入、思考推理、工具调用、JSON 模式与流式输出.",
        model_params=_DOUBAO_SEED_CHAT_PARAMS,
        capabilities=_CHAT_CAPS_MULTIMODAL,
        default_endpoint_name="doubao-seed-2.0-pro",
        # 来源: 火山引擎官方定价(元/百万tokens, 分段计费). 此处取 ≤32K 档作为模型级参考.
        # 完整分段(输入/输出/缓存): ≤32K 3.2/16/0.64; 32K-128K 4.8/24/0.96; 128K-256K 9.6/48/1.92.
        pricing=ModelPricing(input=3.2, output=16.0, cached_input=0.64, currency="CNY"),
    ),
    "doubao-seed-2.0-mini": SharedModel(
        name="Doubao Seed 2.0 Mini",
        model_type=ModelType.CHAT,
        description="字节跳动豆包 Seed 2.0 Mini 轻量均衡模型. 支持图像输入、思考推理、工具调用、JSON 模式与流式输出.",
        model_params=_DOUBAO_SEED_CHAT_PARAMS,
        capabilities=_CHAT_CAPS_MULTIMODAL,
        default_endpoint_name="doubao-seed-2.0-mini",
        # 来源: 火山引擎官方定价(元/百万tokens, 分段计费). 此处取 ≤32K 档作为模型级参考.
        # 完整分段(输入/输出/缓存): ≤32K 0.2/2/0.04; 32K-128K 0.4/4/0.08; 128K-256K 0.8/8/0.16.
        pricing=ModelPricing(input=0.2, output=2.0, cached_input=0.04, currency="CNY"),
    ),
    "doubao-seedream-5.0": SharedModel(
        name="Doubao Seedream 5.0",
        model_type=ModelType.IMAGE_GENERATION,
        description="字节跳动豆包 Seedream 5.0 图片生成模型. 支持文生图, 可输出 URL 或 base64.",
        model_params=_DOUBAO_SEEDREAM_PARAMS,
        capabilities=[
            ModelCapability.TEXT_INPUT,
            ModelCapability.IMAGE_GENERATION,
        ],
        default_endpoint_name="doubao-seedream-5.0",
        # 来源: 火山引擎官方定价. 按张计费(非按 token), 0.22 元/张.
        pricing=ModelPricing(per_call=0.22, currency="CNY"),
    ),
    "doubao-seedance-2.0": SharedModel(
        name="Doubao Seedance 2.0",
        model_type=ModelType.VIDEO_GENERATION,
        description="字节跳动豆包 Seedance 2.0 视频生成模型. 支持文生视频/图生视频, 默认生成有声视频.",
        model_params=_DOUBAO_SEEDANCE_PARAMS,
        capabilities=[
            ModelCapability.TEXT_INPUT,
            ModelCapability.VIDEO_GENERATION,
        ],
        default_endpoint_name="doubao-seedance-2.0",
        # 来源: 火山方舟官方(视频生成按 token 计费, 不含视频输入 46 元/百万token, 含视频输入 28 元/百万token).
        # 此处取不含视频输入的纯文生视频/图生视频价格作为模型级参考.
        pricing=ModelPricing(input=46.0, output=46.0, currency="CNY"),
    ),
    "doubao-seedance-2.0-fast": SharedModel(
        name="Doubao Seedance 2.0 Fast",
        model_type=ModelType.VIDEO_GENERATION,
        description="字节跳动豆包 Seedance 2.0 Fast 快速版视频生成模型. 不支持 1080p, 适合低延迟场景.",
        model_params=_DOUBAO_SEEDANCE_FAST_PARAMS,
        capabilities=[
            ModelCapability.TEXT_INPUT,
            ModelCapability.VIDEO_GENERATION,
        ],
        default_endpoint_name="doubao-seedance-2.0-fast",
        # 来源: 火山引擎官方定价(元/百万tokens, 按 token 总量计费, 无 input/output 拆分).
        # 总 Token = (输入视频时长 + 输出视频时长) × 宽 × 高 × 帧率 / 1024.
        # 含视频输入 22; 此处取不含视频输入(纯文生视频/图生视频)37 作为模型级参考.
        pricing=ModelPricing(input=37.0, output=37.0, currency="CNY"),
    ),
}
