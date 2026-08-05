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

# 需深度安全检查的参数名 (输入清洗越界风险最高)
_DANGEROUS_PARAMS = frozenset({"query", "content", "user_input", "input", "text"})


def _sanitize_params(kwargs: dict[str, Any], strict_mode: bool) -> dict[str, Any]:
    """清理工具参数字典, 对高危参数做深度清洗.

    UnifiedSanitizer 方法均为 classmethod 且无实例状态, 此处独立实例化
    与原内联写法语义等价.
    """
    sanitizer = UnifiedSanitizer()
    safe_kwargs = sanitizer.sanitize_tool_params(kwargs)
    for param in _DANGEROUS_PARAMS:
        if param in safe_kwargs:
            value = safe_kwargs[param]
            if isinstance(value, str) and value.strip():
                UnifiedSanitizer.quick_security_check(value)
                safe_kwargs[param] = sanitizer.sanitize(
                    value,
                    strict_mode=strict_mode,
                ).strip()
    return safe_kwargs


def _sanitize_result(result: Any) -> Any:
    """清理工具输出 (仅 dict/list), 失败时回退原始结果."""
    if not isinstance(result, (dict, list)):
        return result
    try:
        return UnifiedSanitizer().sanitize(result, strict_mode=False)
    except Exception as e:
        logger.warning("输出清理失败,使用原始结果: %s", e)
        return result


def secure_tool_params(
    strict_mode: bool = False,
    sanitize_output: bool = True,
) -> Callable[[Callable[P, Any]], Callable[P, Any]]:
    """工具参数安全装饰器.

    为工具方法提供统一的参数安全检查和清理,3行代码完成集成.

    Args:
        strict_mode: 是否使用严格模式(更严格的验证)
        sanitize_output: 是否清理输出结果

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
                    # 无 kwargs 快速路径: 不做输出 sanitize (保留原行为)
                    if not kwargs:
                        return await func(*args, **kwargs)

                    safe_kwargs = _sanitize_params(kwargs, strict_mode)
                    result = await func(*args, **safe_kwargs)

                    if sanitize_output:
                        result = _sanitize_result(result)
                    return result

                except ValueError as e:
                    logger.error(f"安全检查失败 - {func.__name__}: {e}")
                    raise ValueError(
                        f"参数安全检查失败 (SECURITY_CHECK_FAILED): {e}",
                    ) from e
                except Exception as e:
                    logger.error(f"装饰器执行失败 - {func.__name__}: {e}")
                    raise

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            try:
                # 无 kwargs 快速路径: 不做输出 sanitize (保留原行为)
                if not kwargs:
                    return func(*args, **kwargs)

                safe_kwargs = _sanitize_params(kwargs, strict_mode)
                result = func(*args, **safe_kwargs)

                if sanitize_output:
                    result = _sanitize_result(result)
                return result

            except ValueError as e:
                logger.error(f"安全检查失败 - {func.__name__}: {e}")
                raise ValueError(
                    f"参数安全检查失败 (SECURITY_CHECK_FAILED): {e}",
                ) from e
            except Exception as e:
                logger.error(f"装饰器执行失败 - {func.__name__}: {e}")
                raise

        return sync_wrapper

    return decorator
