"""核心认证管理器

统一认证系统的中央协调器,整合用户管理,安全验证和会话管理.
提供简洁的API接口,隐藏认证复杂性,与现有系统保持兼容.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .models import (
    AuthContext,
    AuthRequest,
    AuthResponse,
    AuthUser,
    StaticUser,
)
from .static_user_manager import StaticUserManager

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """认证错误异常类."""

    def __init__(self, message: str, error_code: str = "AUTH_ERROR") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class AuthorizationError(Exception):
    """授权错误异常类."""

    def __init__(self, message: str, error_code: str = "AUTHZ_ERROR") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class AuthManager:
    """核心认证管理器.

    统一认证系统的中央协调器,提供:
    - API密钥认证
    - 用户会话管理
    - 权限验证
    - 用户隔离保证
    """

    def __init__(self, user_manager: StaticUserManager | None = None) -> None:
        """初始化认证管理器."""
        self.user_manager: StaticUserManager = user_manager or StaticUserManager()
        self._active_sessions: dict[str, AuthContext] = {}

        logger.info("🔐 核心认证管理器初始化完成")

    def authenticate(self, request: AuthRequest) -> AuthResponse:
        """认证请求.

        Args:
            request: 认证请求对象

        Returns:
            认证响应对象

        """
        try:
            # 验证API密钥
            result = self.user_manager.validate_api_key(request.api_key)
            if not result:
                return AuthResponse(
                    success=False,
                    error_message="API密钥无效或已过期",
                    error_code="INVALID_API_KEY",
                )

            user, api_key_info = result

            # 验证用户状态
            if not user.is_active():
                return AuthResponse(
                    success=False,
                    error_message="用户已被禁用",
                    error_code="USER_INACTIVE",
                )

            # 验证用户线程隔离
            if request.user_id or request.thread_id:
                # 如果请求中包含用户ID或线程ID,验证一致性
                if request.user_id and request.user_id != user.user_id:
                    return AuthResponse(
                        success=False,
                        error_message="用户ID不匹配",
                        error_code="USER_ID_MISMATCH",
                    )

                if request.thread_id and request.thread_id != api_key_info.thread_id:
                    return AuthResponse(
                        success=False,
                        error_message="线程ID不匹配",
                        error_code="THREAD_ID_MISMATCH",
                    )

            # 创建认证用户对象
            auth_user = AuthUser(
                user_id=user.user_id,
                thread_id=api_key_info.thread_id,
                display_name=user.display_name,
                permissions=["read", "write"],  # 基础权限
                api_key=request.api_key,
            )

            # 验证请求权限
            if request.permissions:
                for permission in request.permissions:
                    if permission not in auth_user.permissions:
                        return AuthResponse(
                            success=False,
                            error_message=f"权限不足: {permission}",
                            error_code="INSUFFICIENT_PERMISSIONS",
                        )

            logger.info(f"✅ 用户认证成功: {user.user_id} ({api_key_info.thread_id})")

            return AuthResponse(success=True, user=auth_user)

        except ValueError as e:
            logger.error("❌ 认证过程异常: %s", e)
            return AuthResponse(
                success=False,
                error_message="认证过程发生异常",
                error_code="AUTH_EXCEPTION",
            )

    def authenticate_api_key(self, api_key: str) -> AuthUser | None:
        """快速API密钥认证."""
        request = AuthRequest(api_key=api_key)
        response = self.authenticate(request)

        if response.success and response.user:
            return response.user

        return None

    def authenticate_request(self, request: Request) -> tuple[str, str]:
        """从HTTP请求中认证并返回用户ID和线程ID (向后兼容方法)."""
        # 提取API密钥
        api_key = self._extract_api_key_from_request(request)

        if not api_key:
            raise AuthenticationError("API密钥缺失", "MISSING_API_KEY")

        # 认证API密钥
        result = self.user_manager.validate_api_key(api_key)
        if not result:
            raise AuthenticationError("API密钥无效或已过期", "INVALID_API_KEY")

        user, api_key_info = result

        # 验证用户状态
        if not user.is_active():
            raise AuthenticationError("用户已被禁用", "USER_INACTIVE")

        # 验证API密钥是否过期
        if user.is_api_key_expired(api_key):
            raise AuthenticationError("API密钥已过期", "API_KEY_EXPIRED")

        # 更新使用统计
        user.update_usage(api_key)

        logger.debug(
            f"✅ 请求认证成功: 用户={user.user_id}, 线程={api_key_info.thread_id}",
        )

        return user.user_id, api_key_info.thread_id

    def get_user_timezone(self, user_id: str) -> str:
        """根据用户ID获取时区配置, 找不到则返回默认值."""
        try:
            user = self.user_manager.get_user_by_id(user_id)
            if user and hasattr(user, "timezone"):
                return user.timezone
        except Exception as e:
            logger.debug("获取用户时区失败, 使用默认值: %s", e)
        return "Asia/Shanghai"

    def _extract_api_key_from_request(self, request: Request) -> str | None:
        """从HTTP请求中提取API密钥."""
        # 1. Authorization Bearer Token
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header[7:]  # 移除 "Bearer " 前缀

        # 2. X-API-Key Header
        api_key = request.headers.get("x-api-key")
        if api_key:
            return api_key

        # 3. Query Parameter
        api_key = request.query_params.get("api_key")
        if api_key:
            return api_key

        return None

    def create_auth_context(
        self,
        user: AuthUser,
        request_id: str | None = None,
    ) -> AuthContext:
        """创建认证上下文."""
        context = AuthContext(user=user, request_id=request_id)

        # 缓存会话
        session_key = f"{user.user_id}:{user.thread_id}"
        self._active_sessions[session_key] = context

        return context

    def get_auth_context(self, user_id: str, thread_id: str) -> AuthContext | None:
        """获取认证上下文."""
        session_key = f"{user_id}:{thread_id}"
        return self._active_sessions.get(session_key)

    def validate_user_isolation(self, user_id: str, thread_id: str) -> bool:
        """验证用户线程隔离."""
        return self.user_manager.validate_user_isolation(user_id, thread_id)

    def check_permission(self, user: AuthUser, required_permission: str) -> bool:
        """检查用户权限."""
        return user.has_permission(required_permission)

    def enforce_permission(self, user: AuthUser, required_permission: str) -> None:
        """强制权限检查,无权限时抛出异常."""
        if not self.check_permission(user, required_permission):
            raise AuthorizationError(
                f"权限不足: 需要 {required_permission}",
                "PERMISSION_DENIED",
            )

    def get_user_info(self, user_id: str) -> StaticUser | None:
        """获取用户详细信息."""
        return self.user_manager.get_user_by_id(user_id)  # type: ignore[return-value]

    def is_user_active(self, user_id: str) -> bool:
        """检查用户是否活跃."""
        return self.user_manager.is_user_active(user_id)

    def health_check(self) -> dict[str, Any]:
        """健康检查."""
        try:
            # 测试用户管理器
            user_stats = self.user_manager.get_user_statistics()

            return {
                "status": "healthy",
                "user_manager": "operational" if self.user_manager else "error",
                "total_users": user_stats.get("total_users", 0),
                "active_sessions": len(self._active_sessions),
                "timestamp": "2025-01-01T00:00:00Z",  # 可替换为实际时间戳
            }
        except Exception as e:
            logger.error("❌ 认证管理器健康检查失败: %s", e)
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": "2025-01-01T00:00:00Z",
            }


# 全局认证管理器实例
_global_auth_manager: AuthManager | None = None


def get_auth_manager() -> AuthManager:
    """获取全局认证管理器实例."""
    global _global_auth_manager
    if _global_auth_manager is None:
        _global_auth_manager = AuthManager()
    return _global_auth_manager


def reset_auth_manager() -> None:
    """重置全局认证管理器实例(主要用于测试)."""
    global _global_auth_manager
    _global_auth_manager = None


# 便捷函数
def authenticate_api_key(api_key: str) -> AuthUser | None:
    """便捷的API密钥认证函数."""
    auth_manager = get_auth_manager()
    return auth_manager.authenticate_api_key(api_key)


def validate_user_isolation(user_id: str, thread_id: str) -> bool:
    """便捷的用户隔离验证函数."""
    auth_manager = get_auth_manager()
    return auth_manager.validate_user_isolation(user_id, thread_id)


# 导出主要类和函数
__all__ = [
    "AuthManager",
    "AuthenticationError",
    "AuthorizationError",
    "authenticate_api_key",
    "get_auth_manager",
    "reset_auth_manager",
    "validate_user_isolation",
]
