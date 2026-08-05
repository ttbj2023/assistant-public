"""rmw_state 共享工具单元测试 - namespace 化 RMW 锁与后台任务管理."""

from __future__ import annotations

import asyncio

import pytest

from src.agent.memory.rmw_state import (
    clear_module_state,
    get_bg_tasks,
    get_rmw_lock,
    spawn_bg_task,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """每个测试前后清空全部 namespace 状态."""
    clear_module_state()
    yield
    clear_module_state()


def test_lock_namespaced_isolation():
    """不同 namespace 的锁相互独立."""
    lock_a = get_rmw_lock("ns_a", "u", "t", "agent")
    lock_b = get_rmw_lock("ns_b", "u", "t", "agent")
    assert lock_a is not lock_b


def test_lock_lazy_creation_and_reuse():
    """同 namespace + key 二次获取返回同一锁实例."""
    first = get_rmw_lock("pinned", "u", "t", "agent")
    second = get_rmw_lock("pinned", "u", "t", "agent")
    assert first is second


def test_lock_keyed_by_user_thread_agent():
    """同 namespace 不同 user/thread/agent 得到不同锁."""
    lock1 = get_rmw_lock("pinned", "u1", "t", "a")
    lock2 = get_rmw_lock("pinned", "u2", "t", "a")
    assert lock1 is not lock2


def test_clear_namespace_only_leaves_others():
    """指定 namespace 清理不影响其他 namespace."""
    lock_mem = get_rmw_lock("memory", "u", "t", "a")
    clear_module_state("pinned")
    assert get_rmw_lock("memory", "u", "t", "a") is lock_mem


def test_clear_all_when_namespace_none():
    """namespace=None 清空全部."""
    get_rmw_lock("pinned", "u", "t", "a")
    get_rmw_lock("memory", "u", "t", "a")
    clear_module_state()
    # 清空后内部 dict 为空: 重新获取得到全新实例且互不相同
    new_pinned = get_rmw_lock("pinned", "u", "t", "a")
    assert new_pinned is not get_rmw_lock("memory", "u", "t", "a")


@pytest.mark.asyncio
async def test_spawn_drains_and_deregisters():
    """spawn 登记任务, 完成后自动移除."""
    async def _noop() -> None:
        pass

    spawn_bg_task("pinned", _noop())
    assert len(get_bg_tasks("pinned")) == 1
    await asyncio.gather(*get_bg_tasks("pinned"))
    assert len(get_bg_tasks("pinned")) == 0


@pytest.mark.asyncio
async def test_bg_task_namespaced_isolation():
    """bg task 按 namespace 隔离."""
    async def _noop() -> None:
        pass

    spawn_bg_task("pinned", _noop())
    spawn_bg_task("memory", _noop())
    assert len(get_bg_tasks("pinned")) == 1
    assert len(get_bg_tasks("memory")) == 1
    await asyncio.gather(*get_bg_tasks("pinned"), *get_bg_tasks("memory"))
