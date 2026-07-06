"""LLM重试策略单元测试.

测试职责: 验证错误分类函数和失败消息生成函数
测试范围: _is_retryable_llm_exception, _llm_failure_message
"""

from __future__ import annotations

import httpx
import openai

from src.agent.processors.inference_coordinator import (
    _is_retryable_llm_exception,
    _llm_failure_message,
)
from tests.decorators import quick_test


def _make_request() -> httpx.Request:
    """构造 httpx.Request 用于 openai 异常."""
    return httpx.Request("POST", "https://api.example.com/test")


def _make_response(status_code: int = 500) -> httpx.Response:
    """构造 httpx.Response 用于 openai APIStatusError."""
    return httpx.Response(
        status_code=status_code,
        request=_make_request(),
        headers={},
    )


def _make_api_connection_error(
    cause: Exception | None = None,
) -> openai.APIConnectionError:
    """构造 APIConnectionError, 可指定 cause."""
    exc = openai.APIConnectionError(
        message="Connection error.", request=_make_request()
    )
    if cause is not None:
        exc.__cause__ = cause
    return exc


class TestIsRetryableLlmException:
    """_is_retryable_llm_exception 分类测试."""

    @quick_test
    def test_rate_limit_error_retryable(self) -> None:
        """429 限流 → 可重试."""
        exc = openai.RateLimitError(
            "rate limited",
            response=_make_response(429),
            body=None,
        )
        assert _is_retryable_llm_exception(exc) is True

    @quick_test
    def test_internal_server_error_retryable(self) -> None:
        """5xx 服务端错误 → 可重试."""
        exc = openai.InternalServerError(
            "server error",
            response=_make_response(500),
            body=None,
        )
        assert _is_retryable_llm_exception(exc) is True

    @quick_test
    def test_api_connection_error_connect_error_retryable(self) -> None:
        """连接建立失败 (ConnectError) → 可重试."""
        cause = httpx.ConnectError("connection refused")
        exc = _make_api_connection_error(cause=cause)
        assert _is_retryable_llm_exception(exc) is True

    @quick_test
    def test_api_connection_error_connect_timeout_retryable(self) -> None:
        """连接建立超时 (ConnectTimeout) → 可重试."""
        cause = httpx.ConnectTimeout("connect timeout")
        exc = _make_api_connection_error(cause=cause)
        assert _is_retryable_llm_exception(exc) is True

    @quick_test
    def test_api_connection_error_read_error_not_retryable(self) -> None:
        """传输中途 ReadError → 不重试."""
        cause = httpx.ReadError("read error")
        exc = _make_api_connection_error(cause=cause)
        assert _is_retryable_llm_exception(exc) is False

    @quick_test
    def test_api_connection_error_no_cause_not_retryable(self) -> None:
        """APIConnectionError 无 cause → 不重试 (保守)."""
        exc = _make_api_connection_error(cause=None)
        assert _is_retryable_llm_exception(exc) is False

    @quick_test
    def test_api_timeout_error_retryable(self) -> None:
        """send_request 阶段超时 → 可重试.

        APITimeoutError 是 APIConnectionError 的子类,
        必须在 APIConnectionError 之前检查.
        """
        exc = openai.APITimeoutError(request=_make_request())
        assert _is_retryable_llm_exception(exc) is True

    @quick_test
    def test_remote_protocol_error_not_retryable(self) -> None:
        """SSE 流中途断开 (RemoteProtocolError) → 不重试."""
        exc = httpx.RemoteProtocolError("peer closed connection")
        assert _is_retryable_llm_exception(exc) is False

    @quick_test
    def test_httpx_read_error_not_retryable(self) -> None:
        """httpx ReadError 直接逃逸 → 不重试."""
        exc = httpx.ReadError("stream interrupted")
        assert _is_retryable_llm_exception(exc) is False

    @quick_test
    def test_asyncio_timeout_not_retryable(self) -> None:
        """asyncio.wait_for 总时长超限 → 不重试."""
        exc = TimeoutError()
        assert _is_retryable_llm_exception(exc) is False

    @quick_test
    def test_timeout_error_not_retryable(self) -> None:
        """内置 TimeoutError → 不重试."""
        exc = TimeoutError()
        assert _is_retryable_llm_exception(exc) is False

    @quick_test
    def test_bad_request_error_not_retryable(self) -> None:
        """400 BadRequest → 不重试."""
        exc = openai.BadRequestError(
            "bad request",
            response=_make_response(400),
            body=None,
        )
        assert _is_retryable_llm_exception(exc) is False

    @quick_test
    def test_authentication_error_not_retryable(self) -> None:
        """401 Authentication → 不重试."""
        exc = openai.AuthenticationError(
            "invalid key",
            response=_make_response(401),
            body=None,
        )
        assert _is_retryable_llm_exception(exc) is False

    @quick_test
    def test_generic_exception_not_retryable(self) -> None:
        """未知异常 → 不重试 (保守)."""
        exc = RuntimeError("something unexpected")
        assert _is_retryable_llm_exception(exc) is False

    @quick_test
    def test_value_error_not_retryable(self) -> None:
        """ValueError → 不重试."""
        exc = ValueError("invalid value")
        assert _is_retryable_llm_exception(exc) is False


class TestLlmFailureMessage:
    """_llm_failure_message 消息生成测试."""

    @quick_test
    def test_timeout_message(self) -> None:
        """TimeoutError 消息包含"超时"."""
        msg = _llm_failure_message(TimeoutError())
        assert "超时" in msg

    @quick_test
    def test_asyncio_timeout_message(self) -> None:
        """asyncio.TimeoutError 消息包含"超时"."""
        msg = _llm_failure_message(TimeoutError())
        assert "超时" in msg

    @quick_test
    def test_connection_error_connect_failed_message(self) -> None:
        """连接建立失败消息包含"无法连接"."""
        cause = httpx.ConnectError("refused")
        exc = _make_api_connection_error(cause=cause)
        msg = _llm_failure_message(exc)
        assert "无法连接" in msg

    @quick_test
    def test_connection_error_generic_message(self) -> None:
        """APIConnectionError 无 cause → 通用中断消息."""
        exc = _make_api_connection_error(cause=None)
        msg = _llm_failure_message(exc)
        assert "中断" in msg

    @quick_test
    def test_remote_protocol_error_message(self) -> None:
        """RemoteProtocolError 消息包含"中断"."""
        exc = httpx.RemoteProtocolError("peer closed")
        msg = _llm_failure_message(exc)
        assert "中断" in msg

    @quick_test
    def test_rate_limit_message(self) -> None:
        """RateLimitError 消息包含"负载"."""
        exc = openai.RateLimitError(
            "limited",
            response=_make_response(429),
            body=None,
        )
        msg = _llm_failure_message(exc)
        assert "负载" in msg

    @quick_test
    def test_internal_server_error_message(self) -> None:
        """InternalServerError 消息包含"不可用"."""
        exc = openai.InternalServerError(
            "error",
            response=_make_response(500),
            body=None,
        )
        msg = _llm_failure_message(exc)
        assert "不可用" in msg

    @quick_test
    def test_bad_request_message(self) -> None:
        """BadRequestError 消息包含"格式有误"."""
        exc = openai.BadRequestError(
            "bad",
            response=_make_response(400),
            body=None,
        )
        msg = _llm_failure_message(exc)
        assert "格式有误" in msg

    @quick_test
    def test_authentication_message(self) -> None:
        """AuthenticationError 消息包含"认证失败"."""
        exc = openai.AuthenticationError(
            "auth",
            response=_make_response(401),
            body=None,
        )
        msg = _llm_failure_message(exc)
        assert "认证失败" in msg

    @quick_test
    def test_unknown_exception_fallback(self) -> None:
        """未知异常消息包含异常类型名."""
        exc = RuntimeError("unexpected")
        msg = _llm_failure_message(exc)
        assert "RuntimeError" in msg
