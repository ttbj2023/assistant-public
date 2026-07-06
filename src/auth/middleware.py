"""FastAPI统一认证中间件

整合所有认证相关逻辑到单一的FastAPI中间件,替代原有的多层认证实现.
提供API密钥认证,用户隔离和权限验证功能,与现有系统保持兼容.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import (
    APIKeyHeader,
    APIKeyQuery,
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from .auth_manager import AuthManager, get_auth_manager

if TYPE_CHECKING:
    from collections.abc import Callable

    from .models import AuthContext, AuthUser

logger = logging.getLogger(__name__)

# FastAPI内置的多源认证支持
bearer_security = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
api_key_query = APIKeyQuery(name="api_key", auto_error=False)


class FastAPIAuthMiddleware:
    """FastAPI认证中间件.

    统一处理多种API密钥来源:
    - Authorization: Bearer <token>
    - X-API-Key: <token>
    - api_key查询参数
    """

    def __init__(self, auth_manager: AuthManager | None = None) -> None:
        """初始化认证中间件."""
        self.auth_manager = auth_manager or get_auth_manager()
        logger.info("🔐 FastAPI认证中间件初始化完成")

    def extract_api_key(
        self,
        bearer: HTTPAuthorizationCredentials | None = Depends(bearer_security),
        header_key: str | None = Depends(api_key_header),
        query_key: str | None = Depends(api_key_query),
    ) -> str:
        """从多个来源提取API密钥."""
        # 优先级: Bearer Token > X-API-Key Header > Query Parameter
        if bearer:
            return bearer.credentials
        if header_key:
            return header_key
        if query_key:
            return query_key
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API密钥缺失",
            headers={"WWW-Authenticate": "Bearer"},
        )

    async def authenticate_request(
        self,
        _request: Request,
        api_key: str = Depends(extract_api_key),
    ) -> AuthUser:
        """认证HTTP请求."""
        try:
            # 使用认证管理器验证API密钥
            user = self.auth_manager.authenticate_api_key(api_key)
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API密钥无效或已过期",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            logger.debug(f"✅ 请求认证成功: {user.user_id} ({user.thread_id})")
            return user

        except HTTPException:
            raise
        except Exception as e:
            logger.error("❌ 认证过程异常: %s", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="认证服务异常",
            ) from e

    async def create_auth_context(
        self,
        request: Request,
        user: Annotated[AuthUser, Depends(authenticate_request)],
    ) -> AuthContext:
        """创建认证上下文."""
        try:
            # 生成请求ID
            request_id = request.headers.get("X-Request-ID")
            if not request_id:
                import uuid

                request_id = str(uuid.uuid4())

            # 创建认证上下文
            context = self.auth_manager.create_auth_context(
                user=user,
                request_id=request_id,
            )

            # 将上下文添加到请求状态
            request.state.auth_context = context

            return context

        except Exception as e:
            logger.error("❌ 认证上下文创建失败: %s", e)
            # 即使上下文创建失败,也返回基础上下文
            return self.auth_manager.create_auth_context(user=user)


# 创建中间件实例
auth_middleware = FastAPIAuthMiddleware()


# FastAPI依赖注入函数
def get_current_user(
    user: Annotated[AuthUser, Depends(auth_middleware.authenticate_request)],
) -> AuthUser:
    """获取当前认证用户."""
    return user


def get_auth_context(
    context: Annotated[AuthContext, Depends(auth_middleware.create_auth_context)],
) -> AuthContext:
    """获取认证上下文."""
    return context


def get_user_id(user: Annotated[AuthUser, Depends(get_current_user)]) -> str:
    """获取当前用户ID."""
    return user.user_id


def get_thread_id(user: Annotated[AuthUser, Depends(get_current_user)]) -> str:
    """获取当前线程ID."""
    return user.thread_id


def require_permission(permission: str) -> Callable[[Any], Any]:
    """权限检查装饰器工厂."""

    def permission_dependency(
        user: Annotated[AuthUser, Depends(get_current_user)],
    ) -> AuthUser:
        if not user.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足: {permission}",
            )
        return user

    return permission_dependency


# 预定义权限依赖
RequireReadPermission = require_permission("read")
RequireWritePermission = require_permission("write")


class AuthDependency:
    """认证依赖类,提供更灵活的认证配置."""

    def __init__(
        self,
        require_active: bool = True,
        required_permissions: list[str] | None = None,
        auto_create_context: bool = True,
    ) -> None:
        """初始化认证依赖."""
        self.require_active = require_active
        self.required_permissions = required_permissions or []
        self.auto_create_context = auto_create_context

    async def __call__(
        self,
        request: Request,
        api_key: str = Depends(auth_middleware.extract_api_key),
    ) -> AuthUser | AuthContext:
        """执行认证依赖."""
        # 认证用户
        user = await auth_middleware.authenticate_request(request, api_key)

        # 检查用户状态
        if self.require_active:
            auth_manager = get_auth_manager()
            if not auth_manager.is_user_active(user.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="用户已被禁用",
                )

        # 检查权限
        if self.required_permissions:
            auth_manager = get_auth_manager()
            for permission in self.required_permissions:
                auth_manager.enforce_permission(user, permission)

        # 创建上下文(可选)
        if self.auto_create_context:
            return await auth_middleware.create_auth_context(request, user)

        return user


# 便捷的认证依赖
CurrentUser = AuthDependency()
ReadUser = AuthDependency(required_permissions=["read"])
WriteUser = AuthDependency(required_permissions=["write"])
UserOnly = AuthDependency(auto_create_context=False)


# 导出主要组件
__all__ = [
    "AuthDependency",
    "CurrentUser",
    "FastAPIAuthMiddleware",
    "ReadUser",
    "RequireReadPermission",
    "RequireWritePermission",
    "UserOnly",
    "WriteUser",
    "auth_middleware",
    "get_auth_context",
    "get_current_user",
    "get_thread_id",
    "get_user_id",
    "require_permission",
]
