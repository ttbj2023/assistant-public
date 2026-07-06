"""进程级资源生命周期注册表.

重量级资源在创建时自注册 close 回调,
shutdown 时按注册逆序关闭, 异常隔离.

设计要点:
- register(): 资源创建时调用, 注册 close 回调
- close_all(): 按注册逆序执行, 单个失败不中断整体
- DB 连接不纳入注册表, 由 close_all_db_managers() 在 close_all() 之后单独关闭
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

CloseFn = Callable[[], Awaitable[None] | None]


class LifecycleRegistry:
    """进程级资源生命周期注册表.

    资源在创建时通过 register() 注册 close 回调,
    close_all() 按注册逆序执行, 单个失败不中断整体.
    """

    def __init__(self) -> None:
        self._closers: dict[str, CloseFn] = {}
        self._order: list[str] = []

    def register(self, name: str, close_fn: CloseFn) -> None:
        """注册资源 close 回调.

        重复注册同名资源会更新回调但不改变顺序 (幂等).

        Args:
            name: 资源唯一名称 (如 "http_pool", "mcp_bridge")
            close_fn: 关闭回调, 可同步或异步

        """
        if name not in self._closers:
            self._order.append(name)
        self._closers[name] = close_fn

    async def close_all(self) -> None:
        """按注册逆序关闭全部资源, 异常隔离.

        单个资源 close 失败仅记录 warning, 不中断后续资源关闭.
        执行完毕后清空注册表.
        """
        closed = 0
        for name in reversed(self._order):
            close_fn = self._closers.get(name)
            if close_fn is None:
                continue
            try:
                result = close_fn()
                if asyncio.iscoroutine(result):
                    await result
                closed += 1
                logger.debug("已关闭资源: %s", name)
            except Exception as e:
                logger.warning("关闭 %s 异常(非致命): %s", name, e)
        self._closers.clear()
        self._order.clear()
        logger.info("生命周期清理完成: %d 个资源已关闭", closed)


_registry: LifecycleRegistry | None = None


def get_lifecycle_registry() -> LifecycleRegistry:
    """获取全局 LifecycleRegistry 单例."""
    global _registry
    if _registry is None:
        _registry = LifecycleRegistry()
    return _registry


def register_resource(name: str, close_fn: CloseFn) -> None:
    """注册资源到全局 LifecycleRegistry (便捷函数)."""
    get_lifecycle_registry().register(name, close_fn)


def reset_lifecycle_registry() -> None:
    """重置全局注册表 (仅用于测试)."""
    global _registry
    _registry = None
