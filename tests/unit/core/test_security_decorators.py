"""安全装饰器单元测试.

覆盖 secure_tool_params 装饰器的核心逻辑:
- 同步函数装饰
- 异步函数装饰
- 参数清理 (dangerous_params)
- 空kwargs快速路径
- 安全检查失败异常
- 输出清理 (sanitize_output)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.validation.security_decorators import secure_tool_params


class TestSyncDecorator:
    """同步函数装饰器测试."""

    def test_passthrough_no_kwargs(self) -> None:
        @secure_tool_params()
        def target():
            return "ok"

        assert target() == "ok"

    def test_passthrough_empty_kwargs(self) -> None:
        @secure_tool_params()
        def target(**kwargs):
            return kwargs

        result = target(x=1, y=2)
        assert result == {"x": 1, "y": 2}

    @patch("src.core.validation.security_decorators.UnifiedSanitizer")
    def test_dangerous_params_sanitized(self, mock_sanitizer_cls: object) -> None:
        mock_instance = mock_sanitizer_cls.return_value
        mock_instance.sanitize_tool_params.return_value = {
            "query": "safe query",
            "other": "value",
        }
        mock_instance.sanitize.return_value = "cleaned query"

        @secure_tool_params(sanitize_output=False)
        def target(**kwargs):
            return kwargs

        with patch(
            "src.core.validation.security_decorators.UnifiedSanitizer.quick_security_check"
        ):
            result = target(query="test query", other="value")
            assert result["query"] == "cleaned query"

    def test_security_check_failure_raises(self) -> None:
        @secure_tool_params()
        def target(**kwargs):
            return "should not reach"

        with patch(
            "src.core.validation.security_decorators.UnifiedSanitizer"
        ) as mock_cls:
            mock_cls.return_value.sanitize_tool_params.side_effect = ValueError(
                "dangerous input"
            )
            with pytest.raises(ValueError, match="SECURITY_CHECK_FAILED"):
                target(query="<script>alert(1)</script>")

    def test_output_sanitized_when_dict(self) -> None:
        @secure_tool_params()
        def target(**kwargs):
            return {"result": "data"}

        with patch(
            "src.core.validation.security_decorators.UnifiedSanitizer"
        ) as mock_cls:
            mock_inst = mock_cls.return_value
            mock_inst.sanitize_tool_params.return_value = {"x": 1}
            mock_inst.sanitize.return_value = {"result": "cleaned"}
            result = target(x=1)
            assert result == {"result": "cleaned"}

    def test_output_not_sanitized_when_string(self) -> None:
        @secure_tool_params()
        def target(**kwargs):
            return "plain text"

        with patch(
            "src.core.validation.security_decorators.UnifiedSanitizer"
        ) as mock_cls:
            mock_cls.return_value.sanitize_tool_params.return_value = {}
            result = target()
            assert result == "plain text"


class TestAsyncDecorator:
    """异步函数装饰器测试."""

    @pytest.mark.asyncio
    async def test_passthrough_no_kwargs(self) -> None:
        @secure_tool_params()
        async def target():
            return "ok"

        assert await target() == "ok"

    @pytest.mark.asyncio
    async def test_passthrough_with_kwargs(self) -> None:
        @secure_tool_params(sanitize_output=False)
        async def target(**kwargs):
            return kwargs

        result = await target(x=1)
        assert result == {"x": 1}

    @pytest.mark.asyncio
    async def test_security_check_failure_raises(self) -> None:
        @secure_tool_params()
        async def target(**kwargs):
            return "should not reach"

        with patch(
            "src.core.validation.security_decorators.UnifiedSanitizer"
        ) as mock_cls:
            mock_cls.return_value.sanitize_tool_params.side_effect = ValueError(
                "dangerous"
            )
            with pytest.raises(ValueError, match="SECURITY_CHECK_FAILED"):
                await target(query="<script>")

    @pytest.mark.asyncio
    async def test_output_sanitized(self) -> None:
        @secure_tool_params()
        async def target(**kwargs):
            return {"data": "value"}

        with patch(
            "src.core.validation.security_decorators.UnifiedSanitizer"
        ) as mock_cls:
            mock_inst = mock_cls.return_value
            mock_inst.sanitize_tool_params.return_value = {"x": 1}
            mock_inst.sanitize.return_value = {"data": "cleaned"}
            result = await target(x=1)
            assert result == {"data": "cleaned"}


class TestDecoratorOptions:
    """装饰器选项测试."""

    def test_sanitize_output_false(self) -> None:
        @secure_tool_params(sanitize_output=False)
        def target(**kwargs):
            return {"raw": "data"}

        with patch(
            "src.core.validation.security_decorators.UnifiedSanitizer"
        ) as mock_cls:
            mock_cls.return_value.sanitize_tool_params.return_value = {}
            result = target()
            assert result == {"raw": "data"}
