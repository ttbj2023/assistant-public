"""config.yaml 根配置模型.

此模块用于整体校验 config.yaml, 防止未知字段和遗留字段继续漂移. 业务代码仍
通过各子模块 get_config() 读取对应 section.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .api_config import APIConfig
from .auth_config import AuthConfig
from .config_loader import load_base_config_sync
from .core_config import CoreConfig
from .inference_config import InferenceConfig
from .logging_config import LoggingConfig
from .openclaw_config import OpenClawConfig
from .retry_config import RetryConfig
from .smtp_config import SmtpConfig
from .storage_config import StorageConfig
from .tools_config import ToolsConfig


class AppConfig(BaseModel):
    """config.yaml 根 schema."""

    model_config = ConfigDict(extra="forbid")

    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    core: CoreConfig = Field(default_factory=CoreConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    openclaw: OpenClawConfig = Field(default_factory=OpenClawConfig)
    smtp: SmtpConfig = Field(default_factory=SmtpConfig)


_cached: AppConfig | None = None


def load_app_config() -> AppConfig:
    """加载并校验完整 config.yaml."""
    global _cached
    if _cached is None:
        _cached = AppConfig(**load_base_config_sync())
    return _cached


def get_section_config(section: str) -> dict[str, Any]:
    """从根配置返回指定 section 的 dict."""
    cfg = load_app_config()
    value = getattr(cfg, section)
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return {}


def reset_app_config_cache() -> None:
    """重置根配置缓存."""
    global _cached
    _cached = None


__all__ = [
    "AppConfig",
    "get_section_config",
    "load_app_config",
    "reset_app_config_cache",
]
