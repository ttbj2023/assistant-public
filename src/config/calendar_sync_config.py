"""Graph 同步引擎系统级配置 (config.yaml 顶层 calendar_sync 段).

全局默认值, per-user 覆盖存 graph_sync.db 的 graph_sync_settings 表
(todo_list_name / calendar_name / timezone 键).
"""

from __future__ import annotations

from typing import Any, override

from pydantic import Field

from .base_config import BaseConfig
from .config_loader import get_module_config_sync


class CalendarSyncConfig(BaseConfig):
    """Graph 同步全局默认配置."""

    _module_name = "calendar_sync"

    enabled: bool = Field(default=True, description="是否启动同步引擎")
    interval_seconds: float = Field(
        default=600.0,
        ge=30.0,
        description="周期兜底间隔 (秒); 写后即时信号独立加速",
    )
    todo_list_name: str = Field(
        default="Assistant",
        description="远端专用 To Do 清单名",
    )
    calendar_name: str = Field(
        default="Assistant",
        description="远端专用 Outlook 子日历名",
    )
    timezone: str = Field(
        default="Asia/Shanghai",
        description="Graph 请求体时间换算默认时区 (IANA)",
    )

    @classmethod
    @override
    def from_module_config(cls) -> CalendarSyncConfig:
        """从 config.yaml 顶层 calendar_sync.* 块创建配置对象."""
        yaml_config = get_module_config_sync("calendar_sync") or {}
        return cls.from_dict(yaml_config)


# === 配置获取函数 ===

_cached: CalendarSyncConfig | None = None


def get_config() -> CalendarSyncConfig:
    """获取 Graph 同步全局配置对象 (缓存)."""
    global _cached
    if _cached is None:
        _cached = CalendarSyncConfig.from_module_config()
    return _cached


def get_default_config() -> dict[str, Any]:
    """获取本模块默认配置字典 (兜底边界)."""
    return CalendarSyncConfig.get_default_config()


# === 导出接口 ===
__all__ = [
    "CalendarSyncConfig",
    "get_config",
    "get_default_config",
]
