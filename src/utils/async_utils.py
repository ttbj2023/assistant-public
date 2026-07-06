# mypy: ignore-errors
# ParamSpec使用方式与pyright推断不兼容, 运行时行为正确
"""异步工具函数.

提供同步和异步代码之间转换的工具函数,用于处理混合编程模式.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Callable, Coroutine
from contextvars import copy_context
from typing import Any

logger = logging.getLogger(__name__)

# 后台任务强引用集合, 防止 asyncio.create_task 创建的任务被 GC 静默回收.
# 模块级单例, 生命周期与进程一致; 任务完成后通过回调自动移除.
_background_tasks: set[asyncio.Task] = set()


def spawn_background_task(coro: Any) -> asyncio.Task:
    """创建并持有 background task, 防止被 Python GC 回收.

    asyncio.create_task 如果不持有强引用, 在 await 期间可能被 GC 静默回收.
    通过模块级 set 持有引用, 任务完成后自动从集合移除.
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def run_async_in_sync_context[T](
    async_func: Callable[..., Coroutine[Any, Any, T]],
    *args: Any,
    **kwargs: Any,  # type: ignore[misc]
) -> T:
    """在同步上下文中运行异步函数.

    智能处理事件循环冲突:
    - 无运行的事件循环: 使用 asyncio.run()
    - 已有运行的事件循环: 在工作线程的新循环中执行, 避免嵌套冲突

    业务异常按原类型向上传播, 不被吞掉或转换.
    """
    # 仅探测当前线程是否已有运行的事件循环. get_running_loop() 在无运行
    # 循环时抛 RuntimeError, 此处只在探测语句上做最小范围捕获用于决定分支,
    # 绝不覆盖后续线程池内的业务异常.
    has_running_loop = True
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        has_running_loop = False

    if has_running_loop:
        # 已有运行循环: 切到工作线程开新循环执行, 避免嵌套循环冲突.
        # 通过 copy_context() 传递 contextvars (如 UserContext).
        logger.debug("检测到事件循环冲突,使用线程池执行异步操作")
        ctx = copy_context()

        def run_in_new_loop() -> Any:
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return ctx.run(
                    lambda: new_loop.run_until_complete(async_func(*args, **kwargs)),
                )
            finally:
                new_loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_in_new_loop)
            # 业务异常经 future.result() 原样重抛 (类型不变), 交调用方处理.
            return future.result()

    # 无运行循环: 直接 asyncio.run().
    return asyncio.run(async_func(*args, **kwargs))


__all__ = [
    "run_async_in_sync_context",
    "spawn_background_task",
]
