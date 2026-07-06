"""异步工具函数单元测试."""

from __future__ import annotations

import asyncio
import threading
import traceback

import pytest

from src.core.context import (
    UserContext,
    get_user_context,
    reset_user_context,
    set_user_context,
)
from src.utils.async_utils import run_async_in_sync_context


@pytest.mark.asyncio
async def test_run_async_in_sync_context_should_propagate_user_context() -> None:
    """已有事件循环中切到工作线程时应保留 UserContext."""

    async def read_context() -> tuple[str, str, str]:
        ctx = get_user_context()
        return ctx.user_id, ctx.thread_id, ctx.agent_id

    token = set_user_context(
        UserContext(user_id="alice", thread_id="main", agent_id="personal")
    )
    try:
        result = run_async_in_sync_context(read_context)
    finally:
        reset_user_context(token)

    assert result == ("alice", "main", "personal")


@pytest.mark.asyncio
async def test_propagates_runtime_error_from_async_func() -> None:
    """已有循环 + async_func 抛 RuntimeError -> 调用方应收到原始异常."""
    expected = RuntimeError("业务失败: embedding 请求超时")

    async def boom() -> None:
        raise expected

    with pytest.raises(RuntimeError, match="业务失败: embedding 请求超时") as exc_info:
        run_async_in_sync_context(boom)

    # 关键: 验证是原始异常对象本身, 而非 asyncio.run 的
    # "cannot be called from a running event loop" 错误.
    assert exc_info.value is expected


@pytest.mark.asyncio
async def test_propagates_non_runtime_error_from_async_func() -> None:
    """已有循环 + async_func 抛非 RuntimeError -> 正常按原类型传播."""

    async def boom() -> None:
        raise ValueError("参数错误")

    with pytest.raises(ValueError, match="参数错误"):
        run_async_in_sync_context(boom)


@pytest.mark.asyncio
async def test_thread_pool_exception_traceback_preserved() -> None:
    """异常应携带原始 traceback, 便于定位 (回归本次根因)."""

    async def boom() -> None:
        raise RuntimeError("原始位置")

    try:
        run_async_in_sync_context(boom)
    except RuntimeError as e:
        tb_text = "".join(traceback.format_exception(e))
        assert "原始位置" in tb_text
        assert "boom" in tb_text
    else:
        pytest.fail("应当抛出 RuntimeError")


@pytest.mark.asyncio
async def test_no_running_loop_branch_uses_asyncio_run() -> None:
    """无运行循环时走 asyncio.run 分支 (在裸线程中验证)."""

    async def compute() -> str:
        await asyncio.sleep(0)
        return "ok"

    result_box: dict[str, str] = {}
    error_box: dict[str, BaseException] = {}

    def runner() -> None:
        try:
            result_box["v"] = run_async_in_sync_context(compute)
        except BaseException as e:
            error_box["e"] = e

    t = threading.Thread(target=runner)
    t.start()
    t.join()

    assert "e" not in error_box
    assert result_box.get("v") == "ok"
