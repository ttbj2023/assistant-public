"""SMTP 系统级配置测试.

覆盖 resolve_credentials 的回退链:
- username/password/from_address 回退 .env (已有功能, 防回归)
- host 回退 .env SMTP_HOST (新增功能, 让 config.yaml.smtp.host 可留空)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.config.smtp_config import resolve_credentials


class TestResolveCredentialsFallback:
    """resolve_credentials 应按 config.yaml → .env 回退链解析发信凭据."""

    @pytest.fixture
    def _reset_smtp_cache(self):
        from src.config import smtp_config

        smtp_config._cached = None
        yield
        smtp_config._cached = None

    def test_host_falls_back_to_env_when_yaml_empty(
        self, _reset_smtp_cache
    ) -> None:
        """config.yaml.smtp.host 留空时, 回退 .env SMTP_HOST."""
        with (
            patch(
                "src.config.smtp_config.get_module_config_sync",
                return_value={"host": "", "username": "u", "password": "p"},
            ),
            patch.dict("os.environ", {"SMTP_HOST": "smtp.env.example.com"}),
        ):
            creds = resolve_credentials()
        assert creds.host == "smtp.env.example.com"
        assert creds.username == "u"
        assert creds.password == "p"

    def test_host_in_yaml_takes_priority_over_env(self, _reset_smtp_cache) -> None:
        """config.yaml.smtp.host 非空时, 优先于 .env SMTP_HOST."""
        with (
            patch(
                "src.config.smtp_config.get_module_config_sync",
                return_value={
                    "host": "smtp.yaml.example.com",
                    "username": "u",
                    "password": "p",
                },
            ),
            patch.dict("os.environ", {"SMTP_HOST": "smtp.env.example.com"}),
        ):
            creds = resolve_credentials()
        assert creds.host == "smtp.yaml.example.com"

    def test_host_empty_when_both_yaml_and_env_empty(
        self, _reset_smtp_cache
    ) -> None:
        """config.yaml 和 .env 都未配 host 时, 返回空字符串(发邮件会失败)."""
        with (
            patch(
                "src.config.smtp_config.get_module_config_sync",
                return_value={"host": "", "username": "u", "password": "p"},
            ),
            patch.dict("os.environ", {}, clear=True),
        ):
            creds = resolve_credentials()
        assert creds.host == ""

    def test_username_falls_back_to_env(self, _reset_smtp_cache) -> None:
        """username 留空时回退 .env SMTP_USERNAME (已有功能, 防回归)."""
        with (
            patch(
                "src.config.smtp_config.get_module_config_sync",
                return_value={"host": "smtp.test", "username": "", "password": "p"},
            ),
            patch.dict("os.environ", {"SMTP_USERNAME": "env_user"}),
        ):
            creds = resolve_credentials()
        assert creds.username == "env_user"

    def test_from_address_falls_back_to_username(self, _reset_smtp_cache) -> None:
        """from_address 留空时回退 .env SMTP_FROM_ADDRESS, 再回退 username."""
        with (
            patch(
                "src.config.smtp_config.get_module_config_sync",
                return_value={
                    "host": "smtp.test",
                    "username": "fallback_user@test.com",
                    "password": "p",
                },
            ),
            patch.dict("os.environ", {}, clear=True),
        ):
            creds = resolve_credentials()
        assert creds.from_address == "fallback_user@test.com"
