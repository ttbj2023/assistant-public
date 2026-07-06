#!/usr/bin/env python3
"""API模块配置测试.

只保留验证真实配置行为的测试: YAML/env 覆盖优先级、端口校验、URL 生成.
纯默认值/isinstance 回读的测试已删除.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.config.api_config import APIConfig


class TestAPIConfig:
    """API配置主类测试."""

    @patch.dict(os.environ, {}, clear=True)
    @patch("src.config.api_config.get_module_config_sync")
    def test_api_config_yaml_override(self, mock_get_yaml_config) -> None:
        """YAML 配置应覆盖默认值."""
        mock_get_yaml_config.return_value = {
            "host": "0.0.0.0",
            "port": 9000,
        }
        config = APIConfig.from_module_config()

        assert config.host == "0.0.0.0"
        assert config.port == 9000

    @patch.dict(os.environ, {"API_PORT": "7777"})
    @patch("src.config.api_config.get_module_config_sync")
    def test_api_config_runtime_env_override(self, mock_get_yaml_config) -> None:
        """显式运行时环境变量应覆盖 YAML(优先级: env > YAML)."""
        mock_get_yaml_config.return_value = {"port": 9000, "host": "0.0.0.0"}

        config = APIConfig.from_module_config()

        assert config.port == 7777  # 环境变量 > YAML
        assert config.host == "0.0.0.0"  # YAML 配置保留

    def test_api_config_field_validation(self) -> None:
        """端口越界应抛错, host 应被 strip."""
        with pytest.raises(ValueError):
            APIConfig(port=0)

        with pytest.raises(ValueError):
            APIConfig(port=65536)

        config = APIConfig(host="  example.com  ")
        assert config.host == "example.com"

    def test_api_config_get_server_url(self) -> None:
        """服务器 URL 应由 host + port 生成."""
        config = APIConfig(host="localhost", port=8080)
        url = config.get_server_url()
        assert url == "http://localhost:8080"


# 测试标记
pytestmark_unit = pytest.mark.unit
