"""TODO缓存失效回调注册表.

中立解耦点: agent记忆层 (agent.memory.local_memory.cache) 在加载时注册
todolist缓存失效函数, TODO工具层 (tools.internal.todo_manager_base) 通过本注册表
触发失效, 从而消除 tools.internal -> agent 的反向依赖.

注册表只持有 callable 引用, 不依赖任何上层模块.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_invalidator: Callable[..., Any] | None = None


def set_todo_cache_invalidator(fn: Callable[..., Any] | None) -> None:
    """注册todolist缓存失效函数 (由 agent.memory.local_memory.cache 调用).

    传入 None 可清除已注册的函数 (供测试隔离).
    """
    global _invalidator
    _invalidator = fn


def invalidate_todo_cache(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
) -> None:
    """触发todolist缓存失效. 未注册时为no-op (安全降级)."""
    if _invalidator is not None:
        _invalidator(user_id, thread_id, agent_id=agent_id)


__all__ = ["invalidate_todo_cache", "set_todo_cache_invalidator"]
