"""RMW 串行化锁 + fire-and-forget 后台任务管理 (按 namespace 隔离).

供 domain_data 子系统(UserProfileDomainData / InsightsDomainData)及其他
需要串行化覆写的组件共用. 每个 namespace 拥有独立的锁 dict 与 bg task set,
测试可按 namespace 清理.

设计要点:
- 锁按 namespace + user:thread:agent 索引, 杜绝并发改同一块 lost update
- bg task 登记引用防 GC, 完成后自动移除
- 模块级状态跨实例共享(同进程), 测试须用 clear_module_state 隔离
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# namespace -> {lock_key: Lock}; namespace -> {bg_tasks}
_locks: dict[str, dict[str, asyncio.Lock]] = {}
_bg_tasks: dict[str, set[asyncio.Task[None]]] = {}


def _ns_locks(namespace: str) -> dict[str, asyncio.Lock]:
    return _locks.setdefault(namespace, {})


def _ns_bg_tasks(namespace: str) -> set[asyncio.Task[None]]:
    return _bg_tasks.setdefault(namespace, set())


def _lock_key(user_id: str, thread_id: str, agent_id: str) -> str:
    return f"{user_id}:{thread_id}:{agent_id}"


def get_rmw_lock(
    namespace: str,
    user_id: str,
    thread_id: str,
    agent_id: str,
) -> asyncio.Lock:
    """获取 RMW 锁(按 namespace + user:thread:agent 索引, lazy 创建)."""
    locks = _ns_locks(namespace)
    key = _lock_key(user_id, thread_id, agent_id)
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


def spawn_bg_task(namespace: str, coro: Any) -> None:
    """启动 fire-and-forget 后台任务并登记引用防 GC."""
    tasks = _ns_bg_tasks(namespace)
    task = asyncio.create_task(coro)  # type: ignore[arg-type]
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def clear_module_state(namespace: str | None = None) -> None:
    """清理模块级状态(供测试 fixture 使用).

    namespace 指定时只清该 namespace; None 时清空全部.
    """
    if namespace is None:
        for locks in _locks.values():
            locks.clear()
        for tasks in _bg_tasks.values():
            tasks.clear()
        return
    _ns_locks(namespace).clear()
    _ns_bg_tasks(namespace).clear()


def get_bg_tasks(namespace: str) -> set[asyncio.Task[None]]:
    """获取存活后台任务集合(供测试 drain 使用)."""
    return _ns_bg_tasks(namespace)


__all__ = [
    "clear_module_state",
    "get_bg_tasks",
    "get_rmw_lock",
    "spawn_bg_task",
]
