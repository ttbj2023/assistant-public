"""核心系统配置模块.

提供缓存系统的类型安全配置. 配置来源为 config.yaml + Pydantic 默认值.
"""

from __future__ import annotations

from typing import Any, override

from pydantic import BaseModel, Field

from .base_config import BaseConfig
from .config_loader import get_module_config_sync


class CacheConfig(BaseModel):
    """缓存系统配置.

    仅保留记忆系统专用缓存(被 cache.py 直接读取)与全局统计开关.
    客户端/工具/路径等缓存大小经由各自模块内置常量管理, 不在此配置.
    """

    # 记忆系统专用缓存
    pinned_memory_cache_size: int = Field(
        default=50,
        ge=1,
        le=500,
        description="置顶记忆缓存大小",
    )
    conversation_cache_size: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="对话历史缓存大小",
    )

    # 全局缓存统计
    enable_cache_stats: bool = Field(default=True, description="启用缓存统计功能")


class CoreConfig(BaseConfig):
    """核心模块主配置类"""

    _module_name = "core"

    # 嵌套配置对象
    cache: CacheConfig = Field(default_factory=CacheConfig, description="缓存系统配置")

    @classmethod
    @override
    def get_default_config(cls) -> dict[str, Any]:
        """获取core模块的默认配置字典"""
        return cls().model_dump()

    @classmethod
    @override
    def from_module_config(cls) -> CoreConfig:
        """从 config.yaml 创建配置对象.

        Returns:
            配置对象实例

        """
        # 获取YAML配置
        yaml_config = get_module_config_sync("core") or {}

        return cls.from_dict(yaml_config)


# === 配置获取函数 ===


_cached: CoreConfig | None = None


def get_config() -> CoreConfig:
    """获取核心模块配置对象(推荐方式)

    Returns:
        核心配置对象实例

    """
    global _cached
    if _cached is None:
        _cached = CoreConfig.from_module_config()
    return _cached


def get_default_config() -> dict[str, Any]:
    """获取核心模块默认配置字典(兜底边界)

    Returns:
        核心模块默认配置字典

    """
    return CoreConfig.get_default_config()


# === 导出接口 ===
__all__ = [
    "CacheConfig",
    "CoreConfig",
    "get_config",
    "get_default_config",
]
