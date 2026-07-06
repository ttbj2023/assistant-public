"""统一认证管理器测试

测试新的认证体系的核心功能,包括API密钥认证、用户管理和会话管理.
"""

from unittest.mock import MagicMock

import pytest

from src.auth import (
    AuthManager,
    AuthorizationError,
    AuthRequest,
    AuthUser,
    StaticUserManager,
)
from src.auth.models import ApiKeyInfo, StaticUser, UserStatus


class TestAuthManager:
    """认证管理器测试类."""

    @pytest.fixture
    def mock_user_manager(self):
        """模拟用户管理器."""
        manager = MagicMock(spec=StaticUserManager)
        return manager

    @pytest.fixture
    def auth_manager(self, mock_user_manager):
        """创建认证管理器实例."""
        return AuthManager(mock_user_manager)

    @pytest.fixture
    def sample_user(self):
        """示例用户."""
        api_key_info = ApiKeyInfo(
            api_key="sk-project-user1-hash1-abcdef123456",
            thread_id="main",
            description="主线程",
        )
        return StaticUser(
            user_id="testuser",
            display_name="Test User",
            email="test@example.com",
            status=UserStatus.ACTIVE,
            threads=[api_key_info],
        )

    def test_authenticate_success(
        self, auth_manager, mock_user_manager, sample_user
    ) -> None:
        """测试认证成功."""
        # 设置模拟返回 - validate_api_key应该返回(user, api_key_info)元组
        mock_user_manager.validate_api_key.return_value = (
            sample_user,
            sample_user.threads[0],
        )

        # 创建认证请求
        request = AuthRequest(api_key="sk-project-user1-hash1-abcdef123456")

        # 执行认证
        response = auth_manager.authenticate(request)

        # 验证结果
        assert response.success is True
        assert response.user is not None
        assert response.user.user_id == "testuser"
        assert response.user.thread_id == "main"
        assert response.error_message is None

    def test_authenticate_invalid_api_key(
        self, auth_manager, mock_user_manager
    ) -> None:
        """测试无效API密钥."""
        # 设置模拟返回
        mock_user_manager.validate_api_key.return_value = None

        # 创建认证请求
        request = AuthRequest(api_key="invalid-key")

        # 执行认证
        response = auth_manager.authenticate(request)

        # 验证结果
        assert response.success is False
        assert response.user is None
        assert response.error_message == "API密钥无效或已过期"
        assert response.error_code == "INVALID_API_KEY"

    def test_authenticate_inactive_user(
        self, auth_manager, mock_user_manager, sample_user
    ) -> None:
        """测试非活跃用户."""
        # 设置为非活跃用户
        sample_user.status = UserStatus.INACTIVE

        # 设置模拟返回
        mock_user_manager.validate_api_key.return_value = (
            sample_user,
            sample_user.threads[0],
        )

        # 创建认证请求
        request = AuthRequest(api_key="sk-project-user1-hash1-abcdef123456")

        # 执行认证
        response = auth_manager.authenticate(request)

        # 验证结果
        assert response.success is False
        assert response.error_message == "用户已被禁用"
        assert response.error_code == "USER_INACTIVE"

    def test_authenticate_user_id_mismatch(
        self, auth_manager, mock_user_manager, sample_user
    ) -> None:
        """测试用户ID不匹配."""
        # 设置模拟返回
        mock_user_manager.validate_api_key.return_value = (
            sample_user,
            sample_user.threads[0],
        )

        # 创建带有不匹配用户ID的认证请求
        request = AuthRequest(
            api_key="sk-project-user1-hash1-abcdef123456", user_id="different_user"
        )

        # 执行认证
        response = auth_manager.authenticate(request)

        # 验证结果
        assert response.success is False
        assert response.error_message == "用户ID不匹配"
        assert response.error_code == "USER_ID_MISMATCH"

    def test_authenticate_api_key_success(
        self, auth_manager, mock_user_manager, sample_user
    ) -> None:
        """测试快速API密钥认证成功."""
        # 设置模拟返回
        mock_user_manager.validate_api_key.return_value = (
            sample_user,
            sample_user.threads[0],
        )

        # 执行快速认证
        user = auth_manager.authenticate_api_key("sk-project-user1-hash1-abcdef123456")

        # 验证结果
        assert user is not None
        assert user.user_id == "testuser"
        assert user.thread_id == "main"

    def test_authenticate_api_key_failure(
        self, auth_manager, mock_user_manager
    ) -> None:
        """测试快速API密钥认证失败."""
        # 设置模拟返回
        mock_user_manager.validate_api_key.return_value = None

        # 执行快速认证
        user = auth_manager.authenticate_api_key("invalid-key")

        # 验证结果
        assert user is None

    def test_enforce_permission_failure(self, auth_manager) -> None:
        """测试强制权限检查失败."""
        # 创建无权限的用户
        user = AuthUser(
            user_id="testuser",
            thread_id="main",
            display_name="Test User",
            permissions=["read"],
            api_key="sk-project-test",
        )

        # 测试无权限（应该抛出异常）
        with pytest.raises(AuthorizationError) as exc_info:
            auth_manager.enforce_permission(user, "admin")

        assert "权限不足" in str(exc_info.value)
        assert exc_info.value.error_code == "PERMISSION_DENIED"

    def test_health_check_success(self, auth_manager, mock_user_manager) -> None:
        """测试健康检查成功."""
        # 设置模拟返回
        mock_user_manager.get_user_statistics.return_value = {
            "total_users": 10,
            "active_users": 8,
            "total_api_keys": 15,
            "active_api_keys": 12,
        }

        # 执行健康检查
        health = auth_manager.health_check()

        # 验证结果
        assert health["status"] == "healthy"
        assert health["user_manager"] == "operational"
        assert health["total_users"] == 10

    def test_health_check_failure(self, auth_manager, mock_user_manager) -> None:
        """测试健康检查失败."""
        # 设置模拟抛出异常
        mock_user_manager.get_user_statistics.side_effect = Exception("Database error")

        # 执行健康检查
        health = auth_manager.health_check()

        # 验证结果
        assert health["status"] == "unhealthy"
        assert "error" in health
        assert "Database error" in health["error"]
