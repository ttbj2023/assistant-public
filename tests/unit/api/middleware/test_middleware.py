"""认证中间件单元测试.

专注于测试unified_auth_middleware的核心逻辑，Mock所有外部依赖。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import Request

# 导入目标中间件
from src.api.fastapi_app import unified_auth_middleware

# 导入异常类
from src.auth.auth_manager import AuthenticationError


@pytest.mark.unit
class TestUnifiedAuthMiddleware:
    """统一认证中间件单元测试."""

    @pytest.fixture(autouse=True)
    def ensure_clean_auth_state(self, monkeypatch):
        """确保auth_manager未被其他测试污染"""
        import src.api.fastapi_app as fastapi_app_module
        from src.auth import get_auth_manager

        # 恢复真实的auth_manager
        real_auth_manager = get_auth_manager()
        monkeypatch.setattr(fastapi_app_module, "auth_manager", real_auth_manager)

    @pytest.fixture
    def mock_request(self):
        """创建Mock请求对象."""
        request = Mock(spec=Request)
        request.url = Mock()
        request.url.path = "/v1/chat/completions"
        # headers需要是一个Mock对象，其get方法也需要被Mock
        request.headers = Mock()
        request.headers.get = Mock(return_value=None)  # 默认无认证头
        request.query_params = {}
        # state需要能设置属性，使用简单对象
        from types import SimpleNamespace

        request.state = SimpleNamespace()
        return request

    @pytest.fixture
    def mock_call_next(self):
        """创建Mock call_next回调."""

        async def call_next(request):
            response = Mock()
            response.status_code = 200
            return response

        return AsyncMock(side_effect=call_next)

    def test_public_endpoint_health_check_should_skip_auth(
        self, mock_request, mock_call_next
    ):
        """测试公开端点应跳过认证.

        场景：访问健康检查端点
        Given:
            - 请求路径: /health
            - 无需认证头
        When:
            - 调用中间件
        Then:
            - 直接调用call_next，跳过认证
            - 不调用auth_manager
        """
        # Arrange - 设置公开端点路径
        mock_request.url.path = "/health"

        # Act - 执行中间件
        import asyncio

        response = asyncio.run(unified_auth_middleware(mock_request, mock_call_next))

        # Assert - 验证直接调用call_next
        mock_call_next.assert_called_once_with(mock_request)
        assert response.status_code == 200

    def test_valid_authentication_should_set_user_context(
        self, mock_request, mock_call_next
    ):
        """测试有效认证应设置用户上下文.

        场景：使用有效API密钥访问受保护端点
        Given:
            - 请求路径: /v1/chat/completions
            - 有效的Authorization header: Bearer sk-project-alice-main-xxx
            - auth_manager返回: ("alice", "main")
        When:
            - 调用中间件
        Then:
            - 调用auth_manager.authenticate_request()
            - 设置request.state.user_id = "alice"
            - 设置request.state.thread_id = "main"
            - 调用call_next继续处理
        """
        # Arrange - 设置有效API密钥
        mock_request.headers.get.return_value = "Bearer sk-project-alice-main-xxx"

        with patch("src.api.fastapi_app.auth_manager") as mock_auth_manager:
            mock_auth_manager.authenticate_request.return_value = ("alice", "main")

            # Act - 执行中间件
            import asyncio

            response = asyncio.run(
                unified_auth_middleware(mock_request, mock_call_next)
            )

            # Assert - 验证认证流程
            mock_auth_manager.authenticate_request.assert_called_once_with(mock_request)
            assert mock_request.state.user_id == "alice"
            assert mock_request.state.thread_id == "main"
            mock_call_next.assert_called_once_with(mock_request)
            assert response.status_code == 200

    def test_missing_api_key_should_return_401(self, mock_request, mock_call_next):
        """测试缺少API密钥应返回401错误.

        场景：访问受保护端点但缺少API密钥
        Given:
            - 请求路径: /v1/chat/completions
            - 无认证头
            - auth_manager抛出AuthenticationError(MISSING_API_KEY)
        When:
            - 调用中间件
        Then:
            - 返回JSONResponse，状态码401
            - 响应包含 error="API_KEY_MISSING"
            - 响应包含 message="缺少API密钥"
            - 响应包含 hint提示
            - 不调用call_next
        """
        # Arrange - 无认证头
        mock_request.headers.get.return_value = None

        with patch("src.api.fastapi_app.auth_manager") as mock_auth_manager:
            # 模拟认证失败：API密钥缺失（使用中间件能识别的消息）
            mock_auth_manager.authenticate_request.side_effect = AuthenticationError(
                "api密钥缺失", "MISSING_API_KEY"
            )

            # Act - 执行中间件
            import asyncio

            response = asyncio.run(
                unified_auth_middleware(mock_request, mock_call_next)
            )

            # Assert - 验证错误响应
            assert response.status_code == 401
            error_data = response.body.decode()
            error_json = json.loads(error_data)
            assert error_json["error"] == "API_KEY_MISSING"
            assert "缺少API密钥" in error_json["message"]
            assert "hint" in error_json
            assert "example" in error_json
            mock_call_next.assert_not_called()

    def test_invalid_api_key_should_return_401(self, mock_request, mock_call_next):
        """测试无效API密钥应返回401错误.

        场景：使用无效的API密钥
        Given:
            - 请求路径: /v1/chat/completions
            - Authorization header: Bearer invalid-key
            - auth_manager抛出AuthenticationError(INVALID_API_KEY)
        When:
            - 调用中间件
        Then:
            - 返回JSONResponse，状态码401
            - 响应包含 error="API_KEY_INVALID"
            - 响应包含 help提示
        """
        # Arrange - 设置无效API密钥
        mock_request.headers.get.return_value = "Bearer invalid-key"

        with patch("src.api.fastapi_app.auth_manager") as mock_auth_manager:
            # 模拟认证失败：API密钥无效（使用中间件能识别的消息）
            mock_auth_manager.authenticate_request.side_effect = AuthenticationError(
                "api密钥无效", "INVALID_API_KEY"
            )

            # Act - 执行中间件
            import asyncio

            response = asyncio.run(
                unified_auth_middleware(mock_request, mock_call_next)
            )

            # Assert - 验证错误响应
            assert response.status_code == 401
            error_data = response.body.decode()
            error_json = json.loads(error_data)
            assert error_json["error"] == "API_KEY_INVALID"
            assert "API密钥无效" in error_json["message"]
            assert "help" in error_json
            mock_call_next.assert_not_called()

    def test_inactive_user_should_return_403(self, mock_request, mock_call_next):
        """测试用户被禁用应返回403错误.

        场景：API密钥有效但用户已被禁用
        Given:
            - 请求路径: /v1/chat/completions
            - 有效的API密钥
            - auth_manager抛出AuthenticationError(USER_INACTIVE)
        When:
            - 调用中间件
        Then:
            - 返回JSONResponse，状态码403
            - 响应包含 error="USER_INACTIVE"
        """
        # Arrange - 设置有效的API密钥
        mock_request.headers.get.return_value = "Bearer sk-project-alice-main-xxx"

        with patch("src.api.fastapi_app.auth_manager") as mock_auth_manager:
            # 模拟认证失败：用户被禁用（使用中间件能识别的消息）
            mock_auth_manager.authenticate_request.side_effect = AuthenticationError(
                "用户已被禁用", "USER_INACTIVE"
            )

            # Act - 执行中间件
            import asyncio

            response = asyncio.run(
                unified_auth_middleware(mock_request, mock_call_next)
            )

            # Assert - 验证错误响应
            assert response.status_code == 403
            error_data = response.body.decode()
            error_json = json.loads(error_data)
            assert error_json["error"] == "USER_INACTIVE"
            assert "用户账户已被禁用" in error_json["message"]
            mock_call_next.assert_not_called()

    def test_dynamic_api_key_parsing_in_test_env(
        self, mock_request, mock_call_next, monkeypatch
    ):
        """测试测试环境动态解析API Key.

        场景：测试环境禁用静态用户管理
        Given:
            - ENABLE_STATIC_USER_MANAGEMENT=false
            - API Key格式: sk-project-user1-thread1-abc123
        When:
            - 调用中间件
        Then:
            - 解析user_id = "user1"
            - 解析thread_id = "thread1"
            - 设置request.state
            - 不调用auth_manager
        """
        # Arrange - 设置测试环境
        monkeypatch.setenv("ENABLE_STATIC_USER_MANAGEMENT", "false")
        # 使用8个字符的random_suffix（符合代码注释要求）
        mock_request.headers.get.return_value = (
            "Bearer sk-project-user1-thread1-abc12345"
        )

        # Act - 执行中间件（不需要patch auth_manager，动态解析路径不会调用authenticate_request）
        import asyncio

        response = asyncio.run(unified_auth_middleware(mock_request, mock_call_next))

        # Assert - 验证动态解析逻辑
        assert mock_request.state.user_id == "user1"
        assert mock_request.state.thread_id == "thread1"
        mock_call_next.assert_called_once_with(mock_request)
        assert response.status_code == 200
