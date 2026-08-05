"""工具公共运行时行为 - 组合替代继承.

提供自由函数和类装饰器, 供具体工具组合使用 (非继承):
- format_tool_error / format_tool_success: JSON 格式化
- run_sync: sync→async 桥接 + 结果截断 + 错误格式化
- sync_runnable: 类装饰器, 注入 _run 桥接
- inject_identity: 运行时身份注入 (绕过 Pydantic Field)
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def format_tool_error(error: Exception, context: str = "") -> str:
    """统一错误格式化为 JSON.

    Args:
        error: 异常对象
        context: 额外上下文信息

    Returns:
        JSON 格式的错误信息字符串

    """
    result = {
        "success": False,
        "message": f"操作失败: {error!s}",
        "error": f"{type(error).__name__}: {error!s}",
    }
    if context:
        result["context"] = context
    return json.dumps(result, ensure_ascii=False)


def format_tool_success(data: dict[str, Any], message: str = "操作成功") -> str:
    """统一成功格式化为 JSON.

    Args:
        data: 返回数据字典
        message: 成功消息

    Returns:
        JSON 格式的成功信息字符串

    """
    result = {"success": True, "message": message, **data}
    return json.dumps(result, ensure_ascii=False, indent=2)


def run_sync(tool: Any, **kwargs: Any) -> str:
    """sync→async 桥接 + 结果截断 + 错误格式化.

    传递 callable + kwargs 到异步上下文, 截断超长结果.
    桥接失败时返回 JSON 错误.

    """
    try:
        from src.core.text_truncation import truncate_tool_result
        from src.utils.async_utils import run_async_in_sync_context

        result = run_async_in_sync_context(tool._arun, **kwargs)
        return truncate_tool_result(result)
    except Exception as e:
        logger.error(f"{tool.name} 工具同步执行失败: {e}")
        return format_tool_error(e)


def sync_runnable(cls: type) -> type:
    """类装饰器: 注入 _run sync→async 桥接.

    替代基类继承, 具体工具用 @sync_runnable 获得 _run,
    只需实现 _arun().
    """

    def _run(self: Any, **kwargs: Any) -> str:
        return run_sync(self, **kwargs)

    cls._run = _run  # type: ignore[attr-defined]
    # ABCMeta 通过 __abstractmethods__ 判断是否可实例化;
    # 装饰器运行时注入 _run 后需手动移除, 否则类仍被视为抽象
    cls.__abstractmethods__ = frozenset(
        m for m in getattr(cls, "__abstractmethods__", frozenset()) if m != "_run"
    )
    return cls


def inject_identity(
    tool: Any,
    user_id: str,
    thread_id: str,
    agent_id: str,
) -> None:
    """构造后注入运行时身份, 绕过 Pydantic Field 注册.

    user_id/thread_id/agent_id 作为普通属性存在,
    不会出现在 LLM 的 tool schema 中.

    """
    object.__setattr__(tool, "user_id", user_id)
    object.__setattr__(tool, "thread_id", thread_id)
    object.__setattr__(tool, "agent_id", agent_id)


__all__ = [
    "format_tool_error",
    "format_tool_success",
    "inject_identity",
    "run_sync",
    "sync_runnable",
]
