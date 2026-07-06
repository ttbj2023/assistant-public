"""错误处理工具单元测试.

测试 src/api/error_handling/handlers.py 的功能:
- create_standard_error_response: 标准错误响应格式
- get_http_status_for_exception: 异常→HTTP状态码映射
- create_error_response: 完整JSONResponse创建
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from src.api.error_handling.handlers import (
    create_error_response,
    create_standard_error_response,
    get_http_status_for_exception,
)


class TestCreateStandardErrorResponse:
    def test_should_return_standard_format_with_all_fields(self):
        """测试应返回包含所有必需字段的标准格式."""
        exc = ValueError("参数错误")

        result = create_standard_error_response(exc, trace_id="test-trace-123")

        assert result["status"] == "error"
        assert result["error_type"] == "ValueError"
        assert result["message"] == "参数错误"
        assert result["trace_id"] == "test-trace-123"
        assert "timestamp" in result

    def test_should_auto_generate_trace_id_when_not_provided(self):
        """测试未提供trace_id时应自动生成."""
        exc = RuntimeError("运行时错误")

        result = create_standard_error_response(exc)

        assert result["trace_id"] is not None
        assert len(result["trace_id"]) > 0

    def test_should_preserve_exception_class_name(self):
        """测试应保留异常类名."""
        for exc_class, expected_name in [
            (ValueError, "ValueError"),
            (TypeError, "TypeError"),
            (KeyError, "KeyError"),
            (FileNotFoundError, "FileNotFoundError"),
            (RuntimeError, "RuntimeError"),
        ]:
            exc = exc_class("test")
            result = create_standard_error_response(exc)
            assert result["error_type"] == expected_name

    def test_timestamp_should_be_utc_iso_format(self):
        """测试时间戳应为UTC ISO格式."""
        exc = Exception("test")
        before = datetime.now(UTC)

        result = create_standard_error_response(exc)

        after = datetime.now(UTC)
        timestamp = datetime.fromisoformat(result["timestamp"])
        assert before <= timestamp <= after


class TestGetHttpStatusForException:
    def test_http_exception_should_return_its_status_code(self):
        """测试HTTPException应返回自身的状态码."""
        exc = HTTPException(status_code=418, detail="I'm a teapot")
        assert get_http_status_for_exception(exc) == 418

    def test_http_exception_404(self):
        """测试HTTPException 404."""
        exc = HTTPException(status_code=404, detail="Not found")
        assert get_http_status_for_exception(exc) == 404

    def test_client_errors_should_map_to_4xx(self):
        """测试客户端错误应映射到4xx状态码."""
        mappings = {
            ValueError: 400,
            TypeError: 400,
            KeyError: 400,
            AttributeError: 400,
            PermissionError: 403,
            FileNotFoundError: 404,
            LookupError: 404,
        }
        for exc_class, expected_status in mappings.items():
            exc = exc_class("test")
            assert get_http_status_for_exception(exc) == expected_status, (
                f"{exc_class.__name__} should map to {expected_status}"
            )

    def test_server_errors_should_map_to_5xx(self):
        """测试服务端错误应映射到5xx状态码."""
        mappings = {
            RuntimeError: 500,
            ConnectionError: 502,
            TimeoutError: 503,
            OSError: 500,
        }
        for exc_class, expected_status in mappings.items():
            exc = exc_class("test")
            assert get_http_status_for_exception(exc) == expected_status, (
                f"{exc_class.__name__} should map to {expected_status}"
            )

    def test_unknown_exception_should_return_500(self):
        """测试未知异常应返回500."""
        exc = StopAsyncIteration("test")
        assert get_http_status_for_exception(exc) == 500

    def test_base_exception_should_return_500(self):
        """测试基类Exception应返回500."""
        exc = Exception("generic error")
        assert get_http_status_for_exception(exc) == 500


class TestCreateErrorResponse:
    def test_should_return_json_response_with_correct_status(self):
        """测试应返回正确状态码的JSONResponse."""
        exc = ValueError("参数无效")

        response = create_error_response(exc)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 400

    def test_should_use_provided_http_status(self):
        """测试应使用显式提供的HTTP状态码."""
        exc = ValueError("参数无效")

        response = create_error_response(exc, http_status=422)

        assert response.status_code == 422

    def test_should_include_trace_id_in_response(self):
        """测试响应应包含trace_id."""
        exc = RuntimeError("error")

        response = create_error_response(exc, trace_id="trace-abc")

        body = response.body.decode()
        assert "trace-abc" in body

    def test_http_exception_should_include_detail_string(self):
        """测试HTTPException应包含detail字符串."""
        exc = HTTPException(status_code=400, detail="参数验证失败")

        response = create_error_response(exc)

        body = response.body.decode()
        assert "参数验证失败" in body

    def test_http_exception_should_include_detail_dict(self):
        """测试HTTPException应合并detail字典."""
        exc = HTTPException(
            status_code=422,
            detail={"field": "email", "msg": "invalid email"},
        )

        response = create_error_response(exc)

        body = response.body.decode()
        assert "email" in body
        assert "invalid email" in body
