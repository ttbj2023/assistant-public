"""调试配置管理 - 统一 DEBUG 运行时开关.

所有 DEBUG 状态判断均通过 runtime_env.py 的白名单入口读取.
"""

from __future__ import annotations


def is_debug_enabled() -> bool:
    """检查DEBUG模式是否启用."""
    from src.config.runtime_env import is_debug_enabled as _is_debug_enabled

    return _is_debug_enabled()
