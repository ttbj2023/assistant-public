"""统一认证模块

轻量级静态用户认证体系,提供"一个线程对应一个API密钥"的隔离机制.
整合用户管理,安全验证,认证中间件和辅助工具,与现有系统完全兼容.

核心特性:
- 统一的API密钥认证
- 用户线程数据隔离
- 安全验证和清理
- FastAPI中间件集成
- 简洁的管理接口
- 完全向后兼容

主要组件:
- models: 数据模型定义
- security: 安全验证工具
- static_user_manager: 静态用户管理
- auth_manager: 核心认证管理器
- middleware: FastAPI认证中间件
- utils: 认证辅助工具

使用示例:
```python
from src.auth import get_auth_manager, authenticate_api_key

# 认证API密钥
user = authenticate_api_key("sk-project-abc-def-123")

# 或使用认证管理器
auth_manager = get_auth_manager()
user = auth_manager.authenticate_api_key("sk-project-abc-def-123")
```

FastAPI集成示例:
```python
from src.auth import get_current_user, get_auth_context
from src.auth.models import AuthUser, AuthContext

@app.get("/protected")
async def protected_endpoint(
    user: AuthUser = Depends(get_current_user),
    context: AuthContext = Depends(get_auth_context)
):
    return {"message": f"Hello {user.display_name}!"}
```
"""

from __future__ import annotations

import logging

from .auth_manager import (
    AuthenticationError,
    AuthManager,
    AuthorizationError,
    authenticate_api_key,
    get_auth_manager,
    reset_auth_manager,
    validate_user_isolation,
)
from .middleware import (
    AuthDependency,
    CurrentUser,
    FastAPIAuthMiddleware,
    ReadUser,
    RequireReadPermission,
    RequireWritePermission,
    UserOnly,
    WriteUser,
    auth_middleware,
    get_auth_context,
    get_current_user,
    get_thread_id,
    get_user_id,
    require_permission,
)
from .models import (
    ApiKeyInfo,
    AuthRequest,
    AuthResponse,
    StaticUser,
    StaticUserConfig,
    UserStatus,
)
from .models import (
    AuthContext as AuthContextModel,
)
from .models import (
    AuthUser as AuthUserModel,
)
from .security import (
    IDValidator,
    SecuritySanitizer,
    generate_safe_filename,
    secure_api_key_validation,
    secure_user_thread_isolation,
    secure_validate_params,
)
from .static_user_manager import (
    StaticUserManager,
    get_static_user_manager,
)

# 版本信息
__version__ = "1.0.0"
__author__ = "Personal Agent Assistant Team"

# 便捷导入 - 常用类型和函数
__all__ = [
    "ApiKeyInfo",
    "AuthContext",  # 认证上下文
    "AuthDependency",
    # 认证管理
    "AuthManager",
    "AuthRequest",
    "AuthResponse",
    # 核心类型
    "AuthUser",  # 常用的用户类型,使用alias避免命名冲突
    # 异常类
    "AuthenticationError",
    "AuthorizationError",
    "CurrentUser",
    "FastAPIAuthMiddleware",
    # 安全验证
    "IDValidator",
    "ReadUser",
    "RequireReadPermission",
    "RequireWritePermission",
    "SecuritySanitizer",
    "StaticUser",
    "StaticUserConfig",
    # 用户管理
    "StaticUserManager",
    "UserOnly",
    "UserStatus",  # 用户状态枚举
    "WriteUser",
    "__author__",
    # 版本信息
    "__version__",
    "auth_middleware",
    "authenticate_api_key",
    "generate_safe_filename",
    "get_auth_context",
    "get_auth_manager",
    # FastAPI中间件
    "get_current_user",
    "get_static_user_manager",
    "get_thread_id",
    "get_user_id",
    "require_permission",
    "reset_auth_manager",
    "secure_api_key_validation",
    "secure_user_thread_isolation",
    "secure_validate_params",
    "validate_user_isolation",
]

# 类型别名,避免命名冲突
AuthUser = AuthUserModel  # noqa: RUF067
AuthContext = AuthContextModel  # noqa: RUF067

# 模块初始化日志
logger = logging.getLogger(__name__)  # noqa: RUF067
logger.info("🔐 统一认证模块加载完成 v%s", __version__)  # noqa: RUF067
