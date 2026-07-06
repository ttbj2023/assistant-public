"""认证中间件单元测试.

测试 src/auth/middleware.py 的功能:
- FastAPIAuthMiddleware: 初始化和API密钥提取
- require_permission: 权限检查装饰器
- AuthDependency: 灵活认证配置
- get_user_id / get_thread_id: 便捷依赖

Mock边界:
- Mock AuthManager
- Mock FastAPI Request/Response
- 保留真实认证分发逻辑
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import HTTPException

from src.auth.middleware import (
    FastAPIAuthMiddleware,
    require_permission,
)
from src.auth.models import AuthUser


def _create_mock_auth_user(
    user_id="test_user",
    thread_id="main",
    permissions=None,
) -> AuthUser:
    """创建测试用AuthUser."""
    return AuthUser(
        user_id=user_id,
        thread_id=thread_id,
        api_key="sk-project-test-main-abc123",
        display_name="Test User",
        permissions=permissions or ["read", "write"],
        status="active",
    )


@pytest.fixture
def mock_auth_manager():
    """创建Mock AuthManager."""
    manager = MagicMock()
    manager.authenticate_api_key = Mock(return_value=_create_mock_auth_user())
    manager.is_user_active = Mock(return_value=True)
    manager.enforce_permission = Mock()
    manager.create_auth_context = Mock(
        return_value=MagicMock(user_id="test_user", thread_id="main")
    )
    return manager


@pytest.fixture
def middleware(mock_auth_manager):
    """创建FastAPIAuthMiddleware实例."""
    return FastAPIAuthMiddleware(auth_manager=mock_auth_manager)


class TestFastAPIAuthMiddlewareInit:
    @patch("src.auth.middleware.get_auth_manager")
    def test_should_create_default_auth_manager(self, mock_get):
        """测试未提供时应创建默认AuthManager."""
        mock_get.return_value = MagicMock()
        mw = FastAPIAuthMiddleware()
        mock_get.assert_called_once()


class TestFastAPIAuthMiddlewareAuthenticate:
    @pytest.mark.asyncio
    async def test_should_authenticate_valid_api_key(
        self, middleware, mock_auth_manager
    ):
        """测试有效API密钥应认证成功."""
        mock_request = Mock()
        user = await middleware.authenticate_request(
            mock_request, api_key="sk-project-test-main-abc123"
        )

        assert user.user_id == "test_user"
        assert user.thread_id == "main"

    @pytest.mark.asyncio
    async def test_should_reject_invalid_api_key(self, middleware, mock_auth_manager):
        """测试无效API密钥应返回401."""
        mock_auth_manager.authenticate_api_key = Mock(return_value=None)

        mock_request = Mock()
        with pytest.raises(HTTPException) as exc_info:
            await middleware.authenticate_request(mock_request, api_key="invalid-key")

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_should_return_500_on_unexpected_error(
        self, middleware, mock_auth_manager
    ):
        """测试认证异常应返回500."""
        mock_auth_manager.authenticate_api_key = Mock(
            side_effect=RuntimeError("unexpected")
        )

        mock_request = Mock()
        with pytest.raises(HTTPException) as exc_info:
            await middleware.authenticate_request(mock_request, api_key="some-key")

        assert exc_info.value.status_code == 500


class TestRequirePermission:
    def test_should_pass_user_with_permission(self):
        """测试有权限用户应通过."""
        dependency = require_permission("read")
        user = _create_mock_auth_user(permissions=["read", "write"])

        result = dependency(user=user)

        assert result == user

    def test_should_reject_user_without_permission(self):
        """测试无权限用户应拒绝."""
        dependency = require_permission("admin")
        user = _create_mock_auth_user(permissions=["read"])

        with pytest.raises(HTTPException) as exc_info:
            dependency(user=user)

        assert exc_info.value.status_code == 403
        assert "admin" in str(exc_info.value.detail)
