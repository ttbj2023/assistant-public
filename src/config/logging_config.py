"""日志模块配置.

提供日志级别,文件轮转等配置项,支持从 config.yaml 顶层 `logging:` 块读取.
"""

from __future__ import annotations

from typing import override

from pydantic import Field

from .base_config import BaseConfig


class LoggingConfig(BaseConfig):
    """日志配置类.

    对应 config.yaml 顶层 `logging:` 配置块.
    """

    _module_name = "logging"

    level: str = Field(
        default="info",
        description="默认日志级别(debug/info/warning/error)",
    )
    file_max_bytes: int = Field(
        default=20 * 1024 * 1024,
        ge=1,
        description="单个日志文件最大字节数(默认20MB)",
    )
    backup_count: int = Field(
        default=5,
        ge=0,
        description="保留的轮转备份文件数量",
    )

    @classmethod
    @override
    def from_module_config(cls) -> LoggingConfig:
        """从 config.yaml 加载日志配置.

        Returns:
            LoggingConfig 实例
        """
        from .config_loader import get_module_config_sync

        yaml_config = get_module_config_sync("logging") or {}
        return cls.from_dict(yaml_config)


def get_logging_config() -> LoggingConfig:
    """获取日志配置对象便捷入口.

    Returns:
        LoggingConfig 实例
    """
    return LoggingConfig.from_module_config()
