"""生产环境文件日志接线的单元测试.

覆盖 fastapi_app._configure_file_logging: 仅在 ENVIRONMENT=production 时启用
文件日志 (复用 dev_server 已验证的 configure_logging), 避免开发环境重复配置.
"""

from __future__ import annotations

import pytest

from src.api.fastapi_app import _configure_file_logging


class TestConfigureFileLogging:
    """_configure_file_logging 条件分支."""

    @pytest.fixture
    def mock_configure(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
        """mock configure_logging, 记录调用参数."""
        calls: list[tuple] = []

        def fake(level: str, port: int = 0) -> None:
            calls.append((level, port))

        monkeypatch.setattr("src.api.fastapi_app.configure_logging", fake)
        return calls

    def test_production_env_triggers_configure(
        self, mock_configure: list[tuple], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ENVIRONMENT=production 时应调用 configure_logging."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("API_PORT", "8000")

        _configure_file_logging()

        assert mock_configure == [("info", 8000)]

    def test_non_production_env_skipped(
        self, mock_configure: list[tuple], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非生产环境不应配置文件日志 (dev_server 已处理)."""
        monkeypatch.delenv("ENVIRONMENT", raising=False)

        _configure_file_logging()

        assert mock_configure == []

    def test_production_without_api_port_defaults_8000(
        self, mock_configure: list[tuple], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """生产环境未设 API_PORT 时默认 8000."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("API_PORT", raising=False)

        _configure_file_logging()

        assert mock_configure == [("info", 8000)]
