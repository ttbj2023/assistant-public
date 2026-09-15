"""calendar_sync 系统级配置测试.

覆盖 Graph 同步引擎的全局默认段: enabled / interval_seconds / 容器名 / 时区,
以及 AppConfig 根 schema 注册 (未知字段拦截).
"""

from __future__ import annotations

import pytest

from src.config.calendar_sync_config import CalendarSyncConfig, get_config


class TestCalendarSyncConfig:
    """CalendarSyncConfig 解析测试."""

    @pytest.fixture
    def _reset_cache(self):
        from src.config import calendar_sync_config

        calendar_sync_config._cached = None
        yield
        calendar_sync_config._cached = None

    def test_缺省值_引擎默认可运行(self, _reset_cache):
        from unittest.mock import patch

        with patch(
            "src.config.calendar_sync_config.get_module_config_sync",
            return_value=None,
        ):
            cfg = get_config()

        assert cfg.enabled is True
        assert cfg.interval_seconds == 600.0
        assert cfg.todo_list_name == "Assistant"
        assert cfg.calendar_name == "Assistant"
        assert cfg.timezone == "Asia/Shanghai"

    def test_yaml覆盖生效(self, _reset_cache):
        from unittest.mock import patch

        with patch(
            "src.config.calendar_sync_config.get_module_config_sync",
            return_value={
                "enabled": False,
                "interval_seconds": 120,
                "todo_list_name": "助手",
            },
        ):
            cfg = get_config()

        assert cfg.enabled is False
        assert cfg.interval_seconds == 120.0
        assert cfg.todo_list_name == "助手"
        assert cfg.calendar_name == "Assistant"  # 未覆盖走默认

    def test_interval下限校验(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CalendarSyncConfig(interval_seconds=5)

    def test_根schema注册calendar_sync段(self):
        from src.config.app_config import AppConfig

        cfg = AppConfig(calendar_sync={"enabled": False})
        assert cfg.calendar_sync.enabled is False
