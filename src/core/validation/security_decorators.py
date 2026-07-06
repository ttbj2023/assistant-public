"""安全装饰器 - 实用主义设计.

为工具和方法提供统一的安全检查装饰器,3行代码完成集成.
符合项目简单易用,高性能的设计原则.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any, ParamSpec

from .unified_sanitizer import UnifiedSanitizer

logger = logging.getLogger(__name__)

# 参数规范定义
P = ParamSpec("P")


def secure_tool_params(
    strict_mode: bool = False,
    sanitize_output: bool = True,
    _allow_dangerous_keys: list[str] | None = None,
) -> Callable[[Callable[P, Any]], Callable[P, Any]]:
    """工具参数安全装饰器.

    为工具方法提供统一的参数安全检查和清理,3行代码完成集成.

    Args:
        strict_mode: 是否使用严格模式(更严格的验证)
        sanitize_output: 是否清理输出结果
        allow_dangerous_keys: 允许的危险参数键列表(None表示使用默认黑名单)

    Returns:
        装饰后的函数

    使用示例:
        @secure_tool_params()
        async def my_tool(self, *, query: str, **kwargs) -> Any:
            # 所有参数已经过安全检查
            return {"result": "success"}

        @secure_tool_params(strict_mode=True)
        def sensitive_operation(self, user_input: str):
            # 严格模式下的参数检查
            return process(user_input)

    """

    def decorator(func: Callable[P, Any]) -> Callable[P, Any]:
        # 检查函数是否是异步的
        is_async = inspect.iscoroutinefunction(func)

        if is_async:

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                try:
                    # 快速安全检查
                    if not kwargs:
                        return await func(*args, **kwargs)

                    # 创建安全清理器
                    sanitizer = UnifiedSanitizer()

                    # 清理参数字典
                    safe_kwargs = sanitizer.sanitize_tool_params(kwargs)

                    # 对特定参数进行深度安全检查
                    dangerous_params = {
                        "query",
                        "content",
                        "user_input",
                        "input",
                        "text",
                    }
                    for param in dangerous_params:
                        if param in safe_kwargs:
                            value = safe_kwargs[param]
                            if isinstance(value, str) and value.strip():
                                # 快速安全检查
                                UnifiedSanitizer.quick_security_check(value)
                                # 深度清理
                                safe_kwargs[param] = sanitizer.sanitize(
                                    value,
                                    strict_mode=strict_mode,
                                ).strip()

                    # 执行异步原函数
                    result = await func(*args, **safe_kwargs)

                    # 可选:清理输出结果
                    if sanitize_output and isinstance(result, (dict, list)):
                        try:
                            result = sanitizer.sanitize(result, strict_mode=False)
                        except Exception as e:
                            logger.warning("输出清理失败,使用原始结果: %s", e)

                    return result

                except ValueError as e:
                    logger.error(f"安全检查失败 - {func.__name__}: {e}")
                    raise ValueError(
                        f"参数安全检查失败: {e}",
                        "SECURITY_CHECK_FAILED",
                    ) from e
                except Exception as e:
                    logger.error(f"装饰器执行失败 - {func.__name__}: {e}")
                    # 装饰器失败时应该抛出异常,避免静默返回None
                    raise

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            try:
                # 快速安全检查
                if not kwargs:
                    return func(*args, **kwargs)

                # 创建安全清理器
                sanitizer = UnifiedSanitizer()

                # 清理参数字典
                safe_kwargs = sanitizer.sanitize_tool_params(kwargs)

                # 对特定参数进行深度安全检查
                dangerous_params = {
                    "query",
                    "content",
                    "user_input",
                    "input",
                    "text",
                }
                for param in dangerous_params:
                    if param in safe_kwargs:
                        value = safe_kwargs[param]
                        if isinstance(value, str) and value.strip():
                            # 快速安全检查
                            UnifiedSanitizer.quick_security_check(value)
                            # 深度清理
                            safe_kwargs[param] = sanitizer.sanitize(
                                value,
                                strict_mode=strict_mode,
                            ).strip()

                # 执行同步原函数
                result = func(*args, **safe_kwargs)

                # 可选:清理输出结果
                if sanitize_output and isinstance(result, (dict, list)):
                    try:
                        result = sanitizer.sanitize(result, strict_mode=False)
                    except Exception as e:
                        logger.warning("输出清理失败,使用原始结果: %s", e)

                return result

            except ValueError as e:
                logger.error(f"安全检查失败 - {func.__name__}: {e}")
                raise ValueError(
                    f"参数安全检查失败: {e}",
                    "SECURITY_CHECK_FAILED",
                ) from e
            except Exception as e:
                logger.error(f"装饰器执行失败 - {func.__name__}: {e}")
                # 不重新抛出异常,让原函数的错误处理逻辑处理
                raise

        return sync_wrapper

    return decorator
