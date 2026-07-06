#!/usr/bin/env python3
"""OpenClaw 配置测试.

覆盖:
- 默认值创建
- config.yaml 加载
- 异常显式传播
- get_config facade 路由
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.config.openclaw_config import OpenClawConfig


class TestOpenClawConfig:
    """OpenClawConfig 配置类测试."""

    @patch("src.config.openclaw_config.get_module_config_sync")
    def test_default_values(self, mock_get_yaml) -> None:
        """无 config.yaml 时使用默认值."""
        mock_get_yaml.return_value = {}
        config = OpenClawConfig.from_module_config()
        assert isinstance(config.gateway.url, str) and config.gateway.url
        assert config.notification_defaults == {}

    @patch("src.config.openclaw_config.get_module_config_sync")
    def test_load_notification_defaults(self, mock_get_yaml) -> None:
        """从 config.yaml 加载 notification_defaults."""
        mock_get_yaml.return_value = {
            "gateway": {"url": "http://x:18789"},
            "notification_defaults": {
                "weixin": {"channel": "openclaw-weixin"},
            },
        }
        config = OpenClawConfig.from_module_config()
        assert "weixin" in config.notification_defaults
        assert config.notification_defaults["weixin"].channel == "openclaw-weixin"

    @patch("src.config.openclaw_config.get_module_config_sync")
    def test_load_from_yaml(self, mock_get_yaml) -> None:
        """从 config.yaml 加载 gateway 配置."""
        mock_get_yaml.return_value = {
            "gateway": {
                "url": "http://host.docker.internal:18789",
            },
        }
        config = OpenClawConfig.from_module_config()
        assert config.gateway.url == "http://host.docker.internal:18789"

    @patch(
        "src.config.openclaw_config.get_module_config_sync",
        side_effect=RuntimeError("boom"),
    )
    def test_yaml_failure_propagates_exception(self, mock_get_yaml) -> None:
        """config.yaml 加载异常时显式抛出, 不静默兜底."""
        with pytest.raises(RuntimeError, match="boom"):
            OpenClawConfig.from_module_config()
