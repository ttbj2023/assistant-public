"""统一重试策略配置.

按层级分块管理各层调用的重试参数:
- expert_agent: 子 Agent LLM 调用(web_research / geo_research / content_analyzer)
- http_api: 第三方 HTTP API(zhipu / baidu / tencent 等短调用)
- mcp: MCP 工具调用
- grounding: Gemini Grounding(google_search / google_maps)

注: 主 Agent LLM 调用的重试策略由 inference.retry 管理
(见 inference_config.py 的 AgentRetryConfig), 不在此模块.

设计原则:
- LLM 调用最多重试 1 次(避免长任务重试风暴)
- HTTP API 短退避允许多试 2 次(网络抖动场景)
- 限流错误(429)单独退避, 比普通错误等更久
- 不重试客户端错误(4xx 除 429) / SSE 流断 / TimeoutError

配置源: config.yaml 顶层 retry.* 块.
"""

from __future__ import annotations

from typing import Any, override

from pydantic import BaseModel, Field

from .base_config import BaseConfig
from .config_loader import get_module_config_sync


class ExpertAgentRetryConfig(BaseModel):
    """子 Agent LLM 调用重试配置.

    用于 web_research / geo_research / content_analyzer 等专家工具内部的 LLM 调用.
    """

    max_retries: int = Field(default=1, ge=0, description="最大重试次数")
    initial_delay: float = Field(default=2.0, gt=0, description="首次重试延迟(秒)")
    max_delay: float = Field(default=20.0, gt=0, description="退避上限(秒)")


class HttpApiRetryConfig(BaseModel):
    """第三方 HTTP API 重试配置.

    用于 zhipu / baidu / tencent 等短调用的 HTTP API.
    429 限流单独退避, 其他可重试错误按 base_delay * attempt 线性退避.
    """

    max_retries: int = Field(default=2, ge=0, description="最大重试次数")
    base_delay: float = Field(
        default=1.0,
        gt=0,
        description="非限流错误的退避基数(秒, 乘以 attempt 次数)",
    )
    rate_limit_delay: float = Field(
        default=3.0,
        gt=0,
        description="429 限流专用退避(秒, 固定值)",
    )
    retryable_status: list[int] = Field(
        default_factory=lambda: [429, 500, 502, 503, 504],
        description="可重试的 HTTP 状态码",
    )


class McpRetryConfig(BaseModel):
    """MCP 工具调用重试配置."""

    max_retries: int = Field(default=1, ge=0, description="最大重试次数")
    base_delay: float = Field(default=1.0, gt=0, description="非限流错误的退避(秒)")
    rate_limit_delay: float = Field(
        default=3.0,
        gt=0,
        description="429 限流专用退避(秒)",
    )


class GroundingRetryConfig(BaseModel):
    """Gemini Grounding 重试配置(google_search / google_maps).

    Grounding 不在本层重试: Gemini 上游负载(429/503/529)在偶发不可用场景下
    重试收益低且拖慢响应, 失败后由上层 fallback(等效工具链)兜底, 故默认 0 次.
    """

    max_retries: int = Field(default=0, ge=0, description="最大重试次数")


class RetryConfig(BaseConfig):
    """统一重试策略配置.

    管理各层调用的重试参数, 替代散落各处的硬编码常量.
    """

    _module_name = "retry"

    expert_agent: ExpertAgentRetryConfig = Field(
        default_factory=ExpertAgentRetryConfig,
        description="子 Agent LLM 调用重试",
    )
    http_api: HttpApiRetryConfig = Field(
        default_factory=HttpApiRetryConfig,
        description="第三方 HTTP API 重试",
    )
    mcp: McpRetryConfig = Field(
        default_factory=McpRetryConfig,
        description="MCP 工具调用重试",
    )
    grounding: GroundingRetryConfig = Field(
        default_factory=GroundingRetryConfig,
        description="Gemini Grounding 重试",
    )

    @classmethod
    @override
    def from_module_config(cls) -> RetryConfig:
        """从 config.yaml 顶层 retry.* 块创建配置对象."""
        yaml_config = get_module_config_sync("retry") or {}
        return cls.from_dict(yaml_config)


def get_retry_config() -> RetryConfig:
    """获取统一重试配置对象(推荐入口).

    Returns:
        RetryConfig 实例

    """
    return RetryConfig.from_module_config()


def get_http_retry_params() -> dict[str, Any]:
    """便捷获取 HTTP API 重试参数(供 zhipu/baidu/tencent 等使用).

    Returns:
        包含 max_retries / base_delay / rate_limit_delay / retryable_status 的字典

    """
    cfg = get_retry_config().http_api
    return {
        "max_retries": cfg.max_retries,
        "base_delay": cfg.base_delay,
        "rate_limit_delay": cfg.rate_limit_delay,
        "retryable_status": set(cfg.retryable_status),
    }


__all__ = [
    "ExpertAgentRetryConfig",
    "GroundingRetryConfig",
    "HttpApiRetryConfig",
    "McpRetryConfig",
    "RetryConfig",
    "get_http_retry_params",
    "get_retry_config",
]
