"""推理模块配置系统(精简版).

职责边界:
- 管理:使用什么嵌入模型,是否开启向量存储
- 不管理:具体的模型配置参数(维度,批处理大小等)

配置来源: config.yaml + Pydantic 默认值. Provider API Key/base_url 由
provider_registry.py 读取, 不属于 inference 配置字段.
"""

from __future__ import annotations

from typing import Any, override

from pydantic import BaseModel, Field

from .base_config import BaseConfig
from .config_loader import get_module_config_sync


class EmbeddingsConfig(BaseModel):
    """嵌入模型配置(仅管理模型选择)"""

    enabled: bool = Field(
        default=True,
        description="是否启用嵌入模型和向量存储(关闭时向量检索将完全禁用)",
    )
    model: str = Field(
        default="local-embedding:bge-m3",
        description="嵌入模型标识符",
    )


class ContentAnalyzerConfig(BaseModel):
    """内容分析器配置(索引生成,置顶记忆更新等文字分析任务)"""

    model: str = Field(
        default="ark-agent-plan:doubao-seed-2.0-mini",
        description="内容分析器主模型(doubao-seed-2.0-mini, 低成本+JSON mode稳定, 对话索引生成)",
    )
    model_params: dict[str, Any] = Field(
        default_factory=lambda: {
            "max_tokens": 2048,
        },
        description="主模型bind参数(max_tokens=2048; reasoning_effort 用模型注册默认 minimal)",
    )
    pinned_memory_model: str = Field(
        default="",
        description=(
            "置顶记忆每轮更新专属模型(空=使用主model). "
            "置顶更新是精确字符串匹配提取, 区别于索引摘要, 可独立选模型/参数."
        ),
    )
    pinned_memory_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="置顶记忆更新专属bind参数(空=使用主model_params)",
    )
    audit_model: str = Field(
        default="ark-agent-plan:doubao-seed-2.0-pro",
        description="置顶记忆周期审计模型(读全局整理, 区别于每轮model; 评测验证doubao pro precision优)",
    )
    audit_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="审计模型专属bind参数(空则用模型注册默认, doubao pro默认reasoning_effort=medium)",
    )
    arc_model: str = Field(
        default="ark-agent-plan:doubao-seed-2.0-mini",
        description=(
            "索引弧短语蒸馏模型(简单压缩任务, 默认用轻量 minimal-thinking 模型, "
            "区别于 content_analyzer 主模型; 覆盖时 arc_model_params 须与所选模型匹配)"
        ),
    )
    arc_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "弧短语蒸馏模型 bind 参数(空=用模型注册默认; "
            "doubao-seed-2.0-mini 注册默认即 reasoning_effort=minimal, 无需显式设)"
        ),
    )
    fallback_model_params: dict[str, Any] | None = Field(
        default=None,
        description=(
            "判断类任务(置顶记忆更新/SimpleMemory)的 fallback bind 参数覆盖. "
            "None=用全局 inference.fallback.text_model_params(默认关思考, 适合简单任务); "
            "判断类任务需要 fallback 保留思考, 应显式配置(如启用 thinking). "
            "仅作用于判断类任务, 索引生成/弧蒸馏仍走全局默认."
        ),
    )
    dedup_enabled: bool = Field(
        default=True,
        description="是否启用置顶记忆add语义去重(基于嵌入向量)",
    )
    dedup_threshold: float = Field(
        default=0.90,
        ge=0.0,
        le=1.0,
        description="语义去重余弦相似度阈值(越高越严格, 默认0.90)",
    )


class ImageDescriptionConfig(BaseModel):
    """图片描述生成配置(独立于文字分析模型)"""

    model: str = Field(
        default="ark-agent-plan:doubao-seed-2.0-mini",
        description="图片描述主模型(豆包多模态, 优化过)",
    )
    model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="主模型专属bind参数(SDK原生参数名)",
    )
    read_image_model: str = Field(
        default="",
        description="read_image工具专用模型, 空字符串表示跟随model",
    )
    read_image_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="read_image工具专用bind参数, 空dict表示跟随model_params",
    )


class HealthDataExtractionConfig(BaseModel):
    """健康数据提取配置"""

    model: str = Field(
        default="ark-agent-plan:doubao-seed-2.0-mini",
        description="健康数据提取主模型(doubao-seed-2.0-mini, 每轮后台检测+分类+转录)",
    )
    model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="主模型专属bind参数(SDK原生参数名)",
    )
    timeout: float = Field(default=60.0, gt=0, description="API超时时间(秒)")

    audit_model: str = Field(
        default="ark-agent-plan:doubao-seed-2.0-pro",
        description="审计任务专属模型(每10轮触发, 提取本轮新数据+审查历史; 空字符串回退主模型)",
    )
    audit_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="审计模型专属bind参数(SDK原生参数名)",
    )


class ToolFilterConfig(BaseModel):
    """工具发现筛选器配置 (search_available_tools 的 LLM 去噪).

    当关键词匹配返回 >= min_tools_for_filter 个候选工具时,
    调用 LLM 去除明显无关项, 不确定则保留.
    LLM 失败时优雅降级, 返回关键词匹配的全部候选.
    """

    model: str = Field(
        default="local:qwen3:4b-instruct",
        description="工具筛选模型 ID (本地小模型, 短 Prompt + JSON 输出)",
    )
    model_params: dict[str, Any] = Field(
        default_factory=lambda: {
            "format": "json",
            "temperature": 0.0,
            "num_predict": 256,
            "num_ctx": 4096,
        },
        description="筛选模型 bind 参数 (SDK 原生参数名)",
    )
    timeout: float = Field(
        default=5.0,
        gt=0,
        description="LLM 调用超时 (秒), 超时后降级返回全部候选",
    )
    min_tools_for_filter: int = Field(
        default=2,
        ge=1,
        description="触发 LLM 筛选的最少候选工具数",
    )


class ExpertsConfig(BaseModel):
    """专家工具模型配置 - 统一管理各专家工具的LLM模型选择"""

    default_model: str = Field(
        default="deepseek:deepseek-v4-flash",
        description="专家工具全局默认模型",
    )
    default_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="默认模型专属bind参数(SDK原生参数名)",
    )
    web_research_model: str = Field(
        default="",
        description="WebResearchTool模型(空=使用default_model)",
    )
    web_research_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="WebResearch模型专属bind参数(空=使用default_model_params)",
    )
    web_research_synthesis_model: str = Field(
        default="",
        description="WebResearch标准综合模型(空=使用default_model)",
    )
    web_research_synthesis_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="WebResearch标准综合模型bind参数(空=使用default_model_params)",
    )
    geo_research_model: str = Field(
        default="",
        description="GeoResearchTool模型(空=使用default_model)",
    )
    geo_research_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="GeoResearch模型专属bind参数(空=使用default_model_params)",
    )
    professional_database_model: str = Field(
        default="",
        description="ProfessionalDatabaseTool模型(空=使用default_model)",
    )
    professional_database_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="ProfessionalDatabase模型专属bind参数(空=使用default_model_params)",
    )
    grounding_model: str = Field(
        default="gemini:gemini-2.5-flash-lite",
        description="Gemini Grounding模型(需支持google_search权限)",
    )
    grounding_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Grounding模型专属bind参数(temperature/max_output_tokens等)",
    )
    grounding_timeout: float = Field(
        default=90.0,
        description="Grounding单次请求超时(秒, 后端网关会自主重试故留足预算)",
    )
    url_context_enabled: bool = Field(
        default=True,
        description="是否启用 Gemini URL Context 页面理解",
    )
    url_context_model: str = Field(
        default="gemini:gemini-3.1-flash-lite-preview",
        description="Gemini URL Context 模型",
    )
    url_context_quick_timeout: float = Field(
        default=15.0,
        gt=0,
        description="quick 模式 URL Context 单次请求超时(秒)",
    )
    url_context_deep_timeout: float = Field(
        default=20.0,
        gt=0,
        description="deep 模式 URL Context 单次请求超时(秒)",
    )
    url_context_max_urls: int = Field(
        default=4,
        ge=1,
        le=20,
        description="URL Context 单次请求最大 URL 数",
    )
    maps_grounding_model: str = Field(
        default="gemini:gemini-3.1-flash-lite-preview",
        description="Gemini Maps Grounding模型(需支持google_maps权限)",
    )
    maps_grounding_model_params: dict[str, Any] = Field(
        default_factory=lambda: {"temperature": 0.1, "max_output_tokens": 4096},
        description="Maps Grounding模型专属bind参数",
    )
    maps_grounding_timeout: float = Field(
        default=30.0,
        description="Maps Grounding单次请求超时(秒)",
    )
    grounding_fallback_enabled: bool = Field(
        default=True,
        description=(
            "Gemini Grounding(含 search/maps/url_context)不可用时是否启用等效工具 fallback. "
            "关闭后 Gemini 失败直接返回错误, 不降级."
        ),
    )

    def get_model_id(self, tool_name: str) -> str:
        """获取指定专家工具的模型ID, 空值回退到default_model."""
        mapping = {
            "web_research": self.web_research_model,
            "web_research_synthesis": self.web_research_synthesis_model,
            "geo_research": self.geo_research_model,
            "geo_navigator": self.geo_research_model,
            "professional_database": self.professional_database_model,
            "grounding": self.grounding_model,
            "url_context": self.url_context_model,
            "maps_grounding": self.maps_grounding_model,
        }
        model_id = mapping.get(tool_name, "")
        return model_id or self.default_model

    def get_model_params(self, tool_name: str) -> dict[str, Any]:
        """获取指定专家工具的模型bind参数.

        优先使用工具专属参数, 空值回退到default_model_params.
        grounding/maps_grounding使用独立模型体系(Gemini), 不回退到default_model_params.
        """
        if tool_name == "grounding":
            return self.grounding_model_params
        if tool_name == "maps_grounding":
            return self.maps_grounding_model_params
        mapping: dict[str, dict[str, Any]] = {
            "web_research": self.web_research_model_params,
            "web_research_synthesis": self.web_research_synthesis_model_params,
            "geo_research": self.geo_research_model_params,
            "geo_navigator": self.geo_research_model_params,
            "professional_database": self.professional_database_model_params,
        }
        params = mapping.get(tool_name, {})
        return params or self.default_model_params


class ImageGenerationConfig(BaseModel):
    """图片生成模型配置 (统一管理 generate_image 工具和微信发布封面/插图)"""

    model_id: str = Field(
        default="ark-agent-plan:doubao-seedream-5.0-lite",
        description="图片生成模型ID",
    )


class WechatPublishConfig(BaseModel):
    """微信公众号发布配置 (摘要生成/封面提示词/文章润色/自动插图)"""

    model: str = Field(
        default="deepseek:deepseek-v4-flash",
        description="微信发布文字任务模型(摘要/封面提示词/自动插图)",
    )
    model_params: dict[str, Any] = Field(
        default_factory=lambda: {"max_tokens": 4096},
        description="文字模型bind参数",
    )
    refine_model: str = Field(
        default="deepseek:deepseek-v4-pro",
        description="文章润色模型(pro思考模式, 公众号排版优化)",
    )
    refine_model_params: dict[str, Any] = Field(
        default_factory=lambda: {"max_tokens": 32768},
        description="润色模型bind参数",
    )


class FallbackModelConfig(BaseModel):
    """统一 fallback 模型配置(瞬时错误白名单失败时切换的备用模型).

    全局仅两套: 文本 fallback / 视觉 fallback, 所有调用点按任务类型引用.
    配置为空字符串时禁用 fallback(主模型异常直接抛出, 走各调用点现有降级).
    """

    text_model: str = Field(
        default="deepseek:deepseek-v4-flash",
        description="文本任务 fallback 模型(纯文本, 主模型瞬时失败时切换)",
    )
    text_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="文本 fallback 模型 bind 参数(SDK 原生参数名)",
    )
    vision_model: str = Field(
        default="ark-agent-plan:doubao-seed-2.0-mini",
        description="视觉任务 fallback 模型(需 IMAGE_INPUT, 主模型瞬时失败时切换)",
    )
    vision_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="视觉 fallback 模型 bind 参数(SDK 原生参数名)",
    )


class AgentRetryConfig(BaseModel):
    """主 Agent LLM调用重试策略配置.

    此配置控制主 Agent 的整轮执行总预算和 LLM 重试行为.
    子 Agent / 第三方 HTTP / MCP / Grounding 的重试策略见顶层 retry.* (retry_config.py).

    仅对"下次大概率不一样"的错误重试:
    - RateLimitError / InternalServerError (服务端瞬时)
    - APIConnectionError(cause=ConnectError/ConnectTimeout) (连接建立阶段)
    - APITimeoutError (send_request阶段超时)

    其余错误(含SSE流中断/总时长超限/客户端错误)一律不重试.
    """

    max_retries: int = Field(
        default=1,
        description="最大重试次数(仅限可重试错误类型)",
        ge=0,
    )
    total_timeout: float = Field(
        default=900.0,
        description="端到端总预算(秒), 用户发消息到流结束的最大时长",
        gt=0,
    )
    initial_delay: float = Field(
        default=2.0,
        description="首次重试延迟(秒)",
        gt=0,
    )
    max_delay: float = Field(
        default=30.0,
        description="退避上限(秒)",
        gt=0,
    )


class InferenceConfig(BaseConfig):
    """推理模块配置(精简版)

    只管理:
    - 嵌入模型选择
    - 内容分析器模型选择
    - 专家工具模型选择
    - LLM重试策略

    不管理:
    - 具体模型配置参数(维度,批处理大小等)
    - 聊天模型配置(独立管理)
    """

    _module_name = "inference"

    embeddings: EmbeddingsConfig = Field(
        default_factory=EmbeddingsConfig,
        description="嵌入模型配置",
    )
    content_analyzer: ContentAnalyzerConfig = Field(
        default_factory=ContentAnalyzerConfig,
        description="内容分析器配置",
    )
    image_description: ImageDescriptionConfig = Field(
        default_factory=ImageDescriptionConfig,
        description="图片描述生成配置",
    )
    health_data_extraction: HealthDataExtractionConfig = Field(
        default_factory=HealthDataExtractionConfig,
        description="健康数据提取配置",
    )
    tool_filter: ToolFilterConfig = Field(
        default_factory=ToolFilterConfig,
        description="工具发现筛选器配置",
    )
    experts: ExpertsConfig = Field(
        default_factory=ExpertsConfig,
        description="专家工具模型配置",
    )
    image_generation: ImageGenerationConfig = Field(
        default_factory=ImageGenerationConfig,
        description="图片生成模型配置",
    )
    wechat_publish: WechatPublishConfig = Field(
        default_factory=WechatPublishConfig,
        description="微信公众号发布配置",
    )
    retry: AgentRetryConfig = Field(
        default_factory=AgentRetryConfig,
        description="LLM调用重试策略",
    )
    fallback: FallbackModelConfig = Field(
        default_factory=FallbackModelConfig,
        description="统一 fallback 模型配置(瞬时错误备用)",
    )

    @classmethod
    @override
    def from_module_config(cls) -> InferenceConfig:
        """从 config.yaml 创建配置对象."""
        # 获取YAML配置
        yaml_config = get_module_config_sync("inference") or {}

        return cls.from_dict(yaml_config)


_cached: InferenceConfig | None = None


def get_config() -> InferenceConfig:
    """获取推理模块配置对象(推荐方式)

    Returns:
        推理配置对象实例

    """
    global _cached
    if _cached is None:
        _cached = InferenceConfig.from_module_config()
    return _cached


def get_default_config() -> dict[str, Any]:
    """获取推理模块默认配置字典(兜底边界)

    Returns:
        推理模块默认配置字典

    """
    config = InferenceConfig()
    return config.model_dump()


# 导出接口
__all__ = [
    "AgentRetryConfig",
    "ContentAnalyzerConfig",
    "EmbeddingsConfig",
    "ExpertsConfig",
    "FallbackModelConfig",
    "HealthDataExtractionConfig",
    "ImageDescriptionConfig",
    "ImageGenerationConfig",
    "InferenceConfig",
    "ToolFilterConfig",
    "WechatPublishConfig",
    "get_config",
    "get_default_config",
]
