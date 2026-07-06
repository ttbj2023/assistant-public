#!/usr/bin/env python3
"""运行时环境变量兼容测试.

合并原test_environment_variables.py和test_env_manager.py,
消除跨文件重复, 保留有价值的测试:
- 显式 runtime env 覆盖行为
- env_manager底层工具函数(get_env_var)
- Provider URL覆盖
- 边界情况处理
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.config.api_config import get_config as get_api_config
from src.config.auth_config import get_config as get_auth_config
from src.config.env_manager import (
    get_all_env_config,
    get_api_env_config,
    get_env_var,
)
from src.config.inference_config import get_config as get_inference_config

# =============================================================================
# 底层工具函数测试 (原test_env_manager.py)
# =============================================================================


class TestEnvVarUtilities:
    """env_manager底层工具函数测试"""

    @patch.dict(
        os.environ, {"TEST_BOOL": "true", "TEST_INT": "42", "TEST_STR": "hello"}
    )
    def test_get_env_var_type_conversion(self) -> None:
        """测试环境变量类型转换: bool/int/str/default"""
        assert get_env_var("TEST_BOOL", var_type=bool) is True
        assert get_env_var("NONEXISTENT", var_type=bool) is None
        assert get_env_var("NONEXISTENT", default=False, var_type=bool) is False

        assert get_env_var("TEST_INT", var_type=int) == 42
        assert get_env_var("NONEXISTENT", var_type=int) is None
        assert get_env_var("NONEXISTENT", default=0, var_type=int) == 0

        assert get_env_var("TEST_STR") == "hello"
        assert get_env_var("NONEXISTENT") is None
        assert get_env_var("NONEXISTENT", default="default") == "default"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_env_var_boolean_variations(self) -> None:
        """测试布尔值转换的各种形式"""
        for val in ["true", "True", "TRUE", "1", "yes", "YES"]:
            os.environ["TEST_BOOL"] = val
            assert get_env_var("TEST_BOOL", var_type=bool) is True

        for val in ["false", "False", "FALSE", "0", "no", "NO", "anything_else"]:
            os.environ["TEST_BOOL"] = val
            assert get_env_var("TEST_BOOL", var_type=bool) is False

    @patch.dict(os.environ, {}, clear=True)
    def test_get_env_var_invalid_int_raises_error(self) -> None:
        """无效整数环境变量应抛出ValueError"""
        os.environ["INVALID_INT"] = "not_a_number"
        with pytest.raises(ValueError, match="无效的环境变量配置"):
            get_env_var("INVALID_INT", var_type=int)


# =============================================================================
# 运行时环境覆盖测试
# =============================================================================


class TestRuntimeEnvironmentVariables:
    """运行时环境变量测试."""

    @patch.dict(os.environ, {"API_PORT": "9999"}, clear=True)
    @patch("src.config.api_config.get_module_config_sync")
    def test_api_port_runtime_env_should_override_yaml(
        self, mock_get_yaml_config
    ) -> None:
        """API_PORT 是显式允许的运行时覆盖."""
        mock_get_yaml_config.return_value = {"port": 6666}

        config = get_api_config()

        assert config.port == 9999

    @patch.dict(os.environ, {"ENABLE_STATIC_USER_MANAGEMENT": "false"}, clear=True)
    @patch("src.config.auth_config.get_module_config_sync")
    def test_auth_runtime_env_should_override_yaml(self, mock_get_yaml_config) -> None:
        """ENABLE_STATIC_USER_MANAGEMENT 是显式允许的运行时覆盖."""
        mock_get_yaml_config.return_value = {
            "user_management": {"enable_static_user_management": True}
        }

        config = get_auth_config()

        assert config.user_management.enable_static_user_management is False

    @patch("src.config.inference_config.get_module_config_sync")
    def test_inference_config_should_not_use_generic_env_overlay(
        self, mock_get_yaml_config
    ) -> None:
        """推理模型选择只来自 YAML/defaults, 不再支持通用 env overlay."""
        mock_get_yaml_config.return_value = {
            "embeddings": {"model": "yaml:embedding"},
        }

        config = get_inference_config()

        assert config.embeddings.model == "yaml:embedding"

    def test_get_all_env_config_only_reports_allowed_runtime_overrides(self) -> None:
        """兼容 helper 只返回允许的 runtime overlay."""
        with patch.dict(os.environ, {"API_PORT": "8888"}, clear=True):
            all_env = get_all_env_config()

        assert all_env == {"api": {"port": 8888}}


# =============================================================================
# 边界情况测试
# =============================================================================


class TestEnvManagerEdgeCases:
    """环境变量边界情况测试"""

    @patch.dict(os.environ, {"API_PORT": "invalid_port"})
    def test_invalid_api_port_error(self) -> None:
        """无效API端口应抛出ValueError"""
        with pytest.raises(ValueError, match="API_PORT"):
            get_api_env_config()

    @patch.dict(os.environ, {"DEBUG": "invalid_bool"})
    def test_invalid_debug_value_converts_to_false(self) -> None:
        """无效DEBUG值应转换为False"""
        from src.utils.debug_config import is_debug_enabled

        assert is_debug_enabled() is False


pytestmark_unit = pytest.mark.unit
