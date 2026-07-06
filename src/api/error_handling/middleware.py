"""错误处理中间件.

基于标准异常的统一错误处理中间件,替代自定义异常体系.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, override

from starlette.middleware.base import BaseHTTPMiddleware

from .handlers import create_error_response

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import Request, Response


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """错误处理中间件.

    捕获并统一处理应用中发生的所有异常,
    确保返回标准化的错误响应格式.
    """

    @override
    async def dispatch(  # type: ignore[override]
        self,
        request: Request,
        call_next: Callable[[Request], Response],
    ) -> Response:
        """处理请求并捕获异常."""
        try:
            return await call_next(request)  # pyright: ignore[reportGeneralTypeIssues]
        except Exception as exc:
            # 生成trace_id
            trace_id = str(uuid.uuid4())

            # 创建标准错误响应
            error_response = create_error_response(
                exc=exc,
                trace_id=trace_id,
            )

            # 添加请求ID到响应头,便于追踪
            error_response.headers["X-Trace-ID"] = trace_id

            # 记录错误日志
            await self._log_error(request, exc, trace_id)

            return error_response

    async def _log_error(self, request: Request, exc: Exception, trace_id: str) -> None:
        """记录错误日志.

        Args:
            request: 请求对象
            exc: 异常实例
            trace_id: 追踪ID

        """
        logger = logging.getLogger(__name__)

        # 记录基本信息
        logger.error(
            "Request failed",
            extra={
                "trace_id": trace_id,
                "url": str(request.url),
                "method": request.method,
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            },
        )

        # 记录详细错误信息(仅在DEBUG模式)
        if logger.isEnabledFor(logging.DEBUG):
            import traceback

            logger.debug(
                "Error traceback",
                extra={
                    "trace_id": trace_id,
                    "traceback": traceback.format_exc(),
                },
            )
