"""静态用户管理器

负责加载,管理和验证静态用户配置,提供用户-线程-API密钥的映射管理.
支持API密钥验证和用户状态管理等核心功能.

注: API密钥的写操作(创建/撤销/保存)已迁出至 scripts/api_key_manager.py,
本模块仅负责配置加载与读/验证路径.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from src.config.auth_config import AuthConfig as AuthSystemConfig
from src.config.auth_config import get_config

from .models import StaticUserConfig

if TYPE_CHECKING:
    from .models import ApiKeyInfo, StaticUser

logger = logging.getLogger(__name__)


class StaticUserManager:
    """静态用户管理器

    负责管理基于YAML配置的静态用户系统,提供:
    - 配置文件加载和验证
    - API密钥验证和用户查找
    - 用户状态管理
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        enable_validation: bool = True,
    ) -> None:
        """初始化静态用户管理器

        Args:
            config_path: 配置文件路径,默认从环境变量读取
            enable_validation: 是否启用严格验证

        """
        self.enable_validation = enable_validation
        self._config: StaticUserConfig | None = None
        self._config_path: Path

        # 确定配置文件路径
        if config_path:
            self._config_path = Path(config_path)
        else:
            # 使用认证模块配置获取静态用户配置路径
            config: AuthSystemConfig = get_config()
            static_users_path = config.user_management.get_static_users_path()
            # 刻意设计:使用配置文件中的路径,保持配置集中管理
            # 这样可以在不修改代码的情况下调整静态用户文件位置
            self._config_path = Path(static_users_path)

        # 初始化配置
        self._load_config()

        logger.info("🔐 静态用户管理器初始化完成")
        logger.info(f"📁 配置文件: {self._config_path}")
        loaded_config = self._config
        if loaded_config:
            user_count = len(loaded_config.users)
            api_key_count = sum(
                len(user.threads) for user in loaded_config.users.values()
            )
            logger.info("👥 加载用户: %s 个, API密钥: %s 个", user_count, api_key_count)
        else:
            logger.warning("⚠️ 未加载到配置文件,静态用户验证将被禁用")

    def _load_config(self) -> None:
        """加载配置文件"""
        try:
            if not self._config_path.exists():
                logger.warning(f"⚠️ 配置文件不存在: {self._config_path}")
                self._config = None
                return

            with Path(self._config_path).open("r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f)

            if not config_data:
                logger.error("❌ 配置文件为空或格式无效")
                self._config = None
                return

            # 验证和解析配置
            if self.enable_validation:
                try:
                    self._config = StaticUserConfig(**config_data)
                except ValidationError as e:
                    logger.error("❌ 配置文件验证失败: %s", e)
                    self._config = None
                    return
            else:
                # 禁用验证时的简单解析
                self._config = StaticUserConfig.model_validate(config_data)

            logger.info(f"✅ 配置文件加载成功: {self._config_path}")

        except yaml.YAMLError as e:
            logger.error("❌ YAML解析失败: %s", e)
            self._config = None
        except Exception as e:
            logger.error("❌ 配置文件加载失败: %s", e)
            self._config = None

    def is_enabled(self) -> bool:
        """检查静态用户管理是否启用

        Returns:
            是否启用了静态用户管理

        """
        return self._config is not None

    def validate_api_key(self, api_key: str) -> tuple[StaticUser, ApiKeyInfo] | None:
        """验证API密钥并返回用户ID和线程ID

        Args:
            api_key: 要验证的API密钥

        Returns:
            (user_id, thread_id) 元组,验证失败时返回None

        """
        if not self.is_enabled():
            logger.debug("静态用户管理未启用,跳过验证")
            return None

        if not api_key or not isinstance(api_key, str):
            logger.debug("API密钥格式无效")
            return None

        # 验证API密钥
        if self._config is None:
            return None
        result = self._config.validate_api_key(api_key)
        if result:
            user, api_key_info = result
            logger.debug(
                f"✅ API密钥验证成功: {api_key[:20]}... -> {user.user_id}:{api_key_info.thread_id}",
            )
            # 返回完整的用户对象和API密钥信息,供auth_manager使用
            return user, api_key_info

        logger.debug(f"❌ API密钥验证失败: {api_key[:20]}...")
        return None

    def get_user_info(self, user_id: str) -> StaticUser | None:
        """获取用户信息

        Args:
            user_id: 用户ID

        Returns:
            用户信息对象,不存在时返回None

        """
        if not self.is_enabled():
            return None

        if self._config is None:
            return None
        return self._config.get_user_by_user_id(user_id)

    def get_user_by_id(self, user_id: str) -> StaticUser | None:
        """根据用户ID获取用户信息 (兼容别名).

        Args:
            user_id: 用户ID

        Returns:
            用户信息对象,不存在时返回None

        """
        return self.get_user_info(user_id)

    def is_user_active(self, user_id: str) -> bool:
        """检查用户是否活跃.

        Args:
            user_id: 用户ID

        Returns:
            用户是否活跃

        """
        user = self.get_user_info(user_id)
        return user.is_active() if user else False

    def validate_user_isolation(self, user_id: str, thread_id: str) -> bool:
        """验证用户线程隔离.

        Args:
            user_id: 用户ID
            thread_id: 线程ID

        Returns:
            隔离是否有效

        """
        user = self.get_user_info(user_id)
        if not user:
            return False
        return thread_id in user.get_thread_ids()

    def get_all_users(self) -> dict[str, StaticUser]:
        """获取所有用户

        Returns:
            用户字典 {user_id: StaticUser}

        """
        if not self.is_enabled():
            return {}

        if self._config is None:
            return {}
        return self._config.users

    def get_all_api_keys(self) -> dict[str, tuple[str, str]]:
        """获取所有API密钥映射

        Returns:
            API密钥映射 {api_key: (user_id, thread_id)}

        """
        if not self.is_enabled():
            return {}

        if self._config is None:
            return {}
        return self._config.get_all_api_keys()

    def get_user_statistics(self) -> dict[str, Any]:
        """获取用户统计信息

        Returns:
            统计信息字典

        """
        if not self.is_enabled():
            return {
                "enabled": False,
                "total_users": 0,
                "total_api_keys": 0,
                "active_users": 0,
            }

        if self._config is None:
            return {
                "enabled": False,
                "total_users": 0,
                "total_api_keys": 0,
                "active_users": 0,
            }

        users = self._config.users
        total_users = len(users)
        total_api_keys = sum(len(user.threads) for user in users.values())
        active_users = sum(1 for user in users.values() if user.is_active())

        return {
            "enabled": True,
            "config_path": str(self._config_path),
            "total_users": total_users,
            "total_api_keys": total_api_keys,
            "active_users": active_users,
            "inactive_users": total_users - active_users,
        }

    def get_api_key_info(self, api_key: str) -> ApiKeyInfo | None:
        """获取API密钥信息

        Args:
            api_key: API密钥

        Returns:
            API密钥信息或None

        """
        if not self.is_enabled() or self._config is None:
            return None

        result = self._config.get_user_by_api_key(api_key)
        if result:
            _, api_key_info = result
            return api_key_info
        return None


# 全局单例实例
_static_user_manager: StaticUserManager | None = None


def get_static_user_manager() -> StaticUserManager:
    """获取全局静态用户管理器实例

    Returns:
        静态用户管理器单例实例

    """
    global _static_user_manager
    if _static_user_manager is None:
        _static_user_manager = StaticUserManager()
    return _static_user_manager


def is_static_user_management_enabled() -> bool:
    """检查是否启用了静态用户管理

    Returns:
        是否启用了静态用户管理

    """
    # 使用认证模块配置获取静态用户管理启用状态
    config: AuthSystemConfig = get_config()
    return config.user_management.enable_static_user_management


def validate_static_api_key(api_key: str) -> tuple[str, str] | None:
    """验证静态API密钥(便捷函数)

    Args:
        api_key: 要验证的API密钥

    Returns:
        (user_id, thread_id) 元组,验证失败时返回None

    """
    if not is_static_user_management_enabled():
        return None

    manager = get_static_user_manager()
    result = manager.validate_api_key(api_key)
    if result is None:
        return None
    user, key_info = result
    return (user.user_id, key_info.thread_id)


# 导出主要功能
__all__ = [
    "StaticUserManager",
    "get_static_user_manager",
    "is_static_user_management_enabled",
    "validate_static_api_key",
]
