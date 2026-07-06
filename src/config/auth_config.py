"""认证系统配置模块 - Pydantic配置对象版.

常规字段来自 config.yaml + Pydantic 默认值. ENABLE_STATIC_USER_MANAGEMENT
是显式 runtime env 覆盖, 用于测试/E2E和部署开关.

## 使用方式

```python
from src.config.auth_config import get_config, AuthConfig

auth_config = get_config()  # 返回AuthConfig对象

# 访问嵌套配置
print(f"静态用户管理: {auth_config.user_management.enable_static_user_management}")

# 配置方法调用
summary = auth_config.get_config_summary()
config_dict = auth_config.to_dict()
```

## 配置类结构

### AuthConfig (主配置类)
- `user_management`: 用户管理配置 (UserManagementConfig)

### UserManagementConfig (用户管理配置)
- `enable_static_user_management`: 启用静态用户管理

注: API 密钥格式校验由 core/validation/IDValidator 统一负责,
HTTP header/query 提取由 auth_manager 硬编码 (固定协议, 无配置化需求).

## 环境变量配置

支持以下环境变量覆盖配置:
- `ENABLE_STATIC_USER_MANAGEMENT`: 覆盖静态用户管理开关

## YAML配置文件

在项目根目录的config.yaml中配置:

```yaml
auth:
  user_management:
    enable_static_user_management: true
```

## 配置来源

1. config.yaml
2. Pydantic默认值
3. ENABLE_STATIC_USER_MANAGEMENT runtime env 覆盖
"""

from __future__ import annotations

import logging
from typing import Any, override

from pydantic import BaseModel, Field

from .base_config import BaseConfig
from .config_loader import get_module_config_sync
from .runtime_env import get_static_user_management_override

logger = logging.getLogger(__name__)


class UserManagementConfig(BaseModel):
    """用户管理配置."""

    enable_static_user_management: bool = Field(
        default=True,
        description="启用静态用户管理",
    )

    def get_static_users_path(self) -> str:
        """获取静态用户配置文件路径.

        使用src/auth/static_users.yaml作为固定配置文件路径,不支持自定义配置.

        Returns:
            静态用户配置文件路径字符串

        """
        from pathlib import Path

        # 使用项目根目录作为基准
        project_root = Path(__file__).parent.parent.parent
        return str(project_root / "src" / "auth" / "static_users.yaml")


class AuthConfig(BaseConfig):
    """认证模块主配置类."""

    _module_name = "auth"

    # 嵌套配置对象
    user_management: UserManagementConfig = Field(
        default_factory=UserManagementConfig,
        description="用户管理配置",
    )

    @classmethod
    @override
    def from_module_config(cls) -> AuthConfig:
        """从 config.yaml 创建配置对象, 并应用显式 runtime env 覆盖.

        Returns:
            配置对象实例

        """
        # 获取YAML配置
        yaml_config = get_module_config_sync("auth") or {}

        merged_config = yaml_config.copy()
        override = get_static_user_management_override()
        if override is not None:
            merged_config.setdefault("user_management", {})[
                "enable_static_user_management"
            ] = override

        return cls.from_dict(merged_config)


# === 配置获取函数 ===


_cached: AuthConfig | None = None


def get_config() -> AuthConfig:
    """获取认证模块配置对象(推荐方式).

    Returns:
        认证配置对象实例

    """
    global _cached
    if _cached is None:
        _cached = AuthConfig.from_module_config()
    return _cached


def get_default_config() -> dict[str, Any]:
    """获取认证模块默认配置字典(兜底边界).

    Returns:
        认证模块默认配置字典

    """
    return AuthConfig.get_default_config()


# === 导出接口 ===
__all__ = [
    "AuthConfig",
    "UserManagementConfig",
    "get_config",
    "get_default_config",
]
