"""测试装饰器模块.

仅保留 quick_test 装饰器, 用于监控测试执行时间并记录告警.
其余历史装饰器(flaky_retry/retry_test/requires_env_var/performance_test/
integration_test/slow_test/database_test 及 pytest 标记别名)均无引用, 已移除.
"""

import asyncio
import functools
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

# 类型变量定义
F = TypeVar("F", bound=Callable[..., Any])
DecoratorType = Callable[[F], F]

logger = logging.getLogger(__name__)


def quick_test(
    max_execution_time: float | Callable[..., Any] | None = None,
) -> F | DecoratorType:
    """快速测试装饰器, 确保测试执行时间不超过指定限制.

    支持两种调用方式:
    - @quick_test (使用默认5秒限制)
    - @quick_test(max_execution_time=1.0) (指定限制时间)

    Args:
        max_execution_time: 最大允许执行时间(秒), 默认5.0秒

    Returns:
        装饰器函数
    """
    # 处理直接调用 @quick_test 的情况
    if callable(max_execution_time):
        func = max_execution_time
        max_execution_time = 5.0
        return _create_quick_test_wrapper(func, max_execution_time)

    def decorator(func: Callable) -> Callable:
        return _create_quick_test_wrapper(func, max_execution_time)

    return decorator


def _create_quick_test_wrapper[F: Callable[..., Any]](
    func: F, max_execution_time: float
) -> F:
    """创建quick_test装饰器的包装器函数.

    Args:
        func: 被装饰的函数
        max_execution_time: 最大执行时间限制

    Returns:
        装饰后的函数
    """

    @functools.wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            execution_time = time.time() - start_time

            if execution_time > max_execution_time:
                logger.warning(
                    f"测试 {func.__name__} 执行时间过长: {execution_time:.2f}s "
                    f"(限制: {max_execution_time}s)"
                )

            return result
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"测试 {func.__name__} 在 {execution_time:.2f}s 后失败: {e}")
            raise

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            execution_time = time.time() - start_time

            if execution_time > max_execution_time:
                logger.warning(
                    f"测试 {func.__name__} 执行时间过长: {execution_time:.2f}s "
                    f"(限制: {max_execution_time}s)"
                )

            return result
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"测试 {func.__name__} 在 {execution_time:.2f}s 后失败: {e}")
            raise

    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper
