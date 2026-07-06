"""认证系统配置测试.

只保留验证真实配置行为的测试: 嵌套合并、update 警告.
纯 Pydantic isinstance/默认值回读的测试已删除.
"""

import pytest

from src.config.auth_config import (
    AuthConfig,
    UserManagementConfig,
)


class TestAuthConfigClasses:
    """测试Auth配置类."""

    def test_auth_config_from_dict(self) -> None:
        """从字典创建配置应正确合并嵌套字段."""
        config_dict = {
            "user_management": {"enable_static_user_management": False},
        }

        config = AuthConfig.from_dict(config_dict)
        assert config.user_management.enable_static_user_management is False

    def test_config_update(self) -> None:
        """更新现有字段生效, 更新不存在字段应发出警告."""
        config = AuthConfig()

        updated = config.update(
            user_management=UserManagementConfig(enable_static_user_management=False)
        )
        assert updated.user_management.enable_static_user_management is False

        with pytest.warns(UserWarning, match="配置字段 'nonexistent_field' 不存在"):
            updated = config.update(nonexistent_field="value")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
