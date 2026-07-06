"""统一错误处理工具.

提供标准化的错误响应格式,统一处理各种异常类型,
包括标准Python异常,FastAPI异常和LangChain异常.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse


def create_standard_error_response(
    exc: Exception,
    trace_id: str | None = None,
    _http_status: int | None = None,
) -> dict[str, Any]:
    """创建标准错误响应格式.

    Args:
        exc: 异常实例
        trace_id: 追踪ID,如未提供则自动生成
        http_status: HTTP状态码,如未提供则根据异常类型推断

    Returns:
        标准化的错误响应字典

    """
    return {
        "status": "error",
        "error_type": exc.__class__.__name__,
        "message": str(exc),
        "trace_id": trace_id or str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
    }


def get_http_status_for_exception(exc: Exception) -> int:
    """根据异常类型获取对应的HTTP状态码.

    Args:
        exc: 异常实例

    Returns:
        HTTP状态码

    """
    # FastAPI HTTPException
    if isinstance(exc, HTTPException):
        return exc.status_code

    # 标准Python异常映射
    status_mapping = {
        # 客户端错误 (4xx)
        ValueError: 400,  # 参数验证错误
        TypeError: 400,  # 类型错误
        KeyError: 400,  # 键错误
        AttributeError: 400,  # 属性错误
        PermissionError: 403,  # 权限不足
        FileNotFoundError: 404,  # 文件不存在
        LookupError: 404,  # 查找错误
        # 服务器错误 (5xx)
        RuntimeError: 500,  # 运行时错误
        ConnectionError: 502,  # 连接错误
        TimeoutError: 503,  # 超时错误
        OSError: 500,  # 操作系统错误
        IOError: 500,  # 输入输出错误
        Exception: 500,  # 通用异常
    }

    # LangChain异常映射
    if hasattr(exc, "__module__") and "langchain" in exc.__module__:
        if "LLMError" in str(type(exc)):
            return 502  # LLM服务错误
        if "ToolError" in str(type(exc)):
            return 500  # 工具执行错误
        if "MemoryError" in str(type(exc)):
            return 500  # 记忆系统错误
        return 500  # 其他LangChain错误

    # 根据异常类型获取状态码
    for exc_type, status in status_mapping.items():
        if isinstance(exc, exc_type):
            return status

    # 默认返回500
    return 500


def create_error_response(
    exc: Exception,
    trace_id: str | None = None,
    http_status: int | None = None,
) -> JSONResponse:
    """创建错误响应.

    Args:
        exc: 异常实例
        trace_id: 追踪ID
        http_status: HTTP状态码

    Returns:
        JSONResponse

    """
    if http_status is None:
        http_status = get_http_status_for_exception(exc)

    response_data = create_standard_error_response(exc, trace_id)

    # 如果是HTTPException,添加detail信息
    if isinstance(exc, HTTPException) and hasattr(exc, "detail"):
        if isinstance(exc.detail, dict):
            response_data.update(exc.detail)
        else:
            response_data["detail"] = exc.detail

    return JSONResponse(
        status_code=http_status,
        content=response_data,
    )
