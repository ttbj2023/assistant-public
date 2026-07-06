"""错误处理中间件单元测试.

测试 src/api/error_handling/middleware.py 的 ErrorHandlingMiddleware:
- 正常请求透传
- 异常捕获和标准错误响应生成
- trace_id生成和响应头设置
- 错误日志记录
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

from src.api.error_handling.middleware import ErrorHandlingMiddleware


@pytest.fixture
def middleware():
    """创建中间件实例."""
    mock_app = Mock()
    return ErrorHandlingMiddleware(mock_app)


@pytest.fixture
def mock_request():
    """创建Mock请求对象."""
    request = Mock(spec=Request)
    request.url = Mock()
    request.url.path = "/v1/chat/completions"
    request.method = "POST"
    return request


class TestErrorHandlingMiddleware:
    @pytest.mark.asyncio
    async def test_normal_request_should_pass_through(self, middleware, mock_request):
        """测试正常请求应透传到下游."""
        expected_response = Mock()
        expected_response.status_code = 200
        call_next = AsyncMock(return_value=expected_response)

        result = await middleware.dispatch(mock_request, call_next)

        call_next.assert_called_once_with(mock_request)
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_exception_should_return_error_response(
        self, middleware, mock_request
    ):
        """测试异常应返回标准错误响应."""
        call_next = AsyncMock(side_effect=ValueError("参数无效"))

        result = await middleware.dispatch(mock_request, call_next)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 400

    @pytest.mark.asyncio
    async def test_exception_should_include_trace_id_header(
        self, middleware, mock_request
    ):
        """测试异常响应应包含X-Trace-ID响应头."""
        call_next = AsyncMock(side_effect=RuntimeError("服务器错误"))

        result = await middleware.dispatch(mock_request, call_next)

        assert "X-Trace-ID" in result.headers
        trace_id = result.headers["X-Trace-ID"]
        assert len(trace_id) > 0

    @pytest.mark.asyncio
    async def test_exception_should_include_trace_id_in_body(
        self, middleware, mock_request
    ):
        """测试异常响应体应包含trace_id."""
        call_next = AsyncMock(side_effect=RuntimeError("错误"))

        result = await middleware.dispatch(mock_request, call_next)

        body = result.body.decode()
        assert "trace_id" in body

    @pytest.mark.asyncio
    async def test_server_exception_should_return_500(self, middleware, mock_request):
        """测试服务端异常应返回500."""
        call_next = AsyncMock(side_effect=RuntimeError("内部错误"))

        result = await middleware.dispatch(mock_request, call_next)

        assert result.status_code == 500

    @pytest.mark.asyncio
    async def test_connection_error_should_return_502(self, middleware, mock_request):
        """测试连接错误应返回502."""
        call_next = AsyncMock(side_effect=ConnectionError("连接失败"))

        result = await middleware.dispatch(mock_request, call_next)

        assert result.status_code == 502

    @pytest.mark.asyncio
    async def test_timeout_should_return_503(self, middleware, mock_request):
        """测试超时应返回503."""
        call_next = AsyncMock(side_effect=TimeoutError("请求超时"))

        result = await middleware.dispatch(mock_request, call_next)

        assert result.status_code == 503

    @pytest.mark.asyncio
    async def test_error_response_body_should_contain_error_type(
        self, middleware, mock_request
    ):
        """测试错误响应体应包含错误类型."""
        call_next = AsyncMock(side_effect=ValueError("值错误"))

        result = await middleware.dispatch(mock_request, call_next)

        body = result.body.decode()
        assert "ValueError" in body
