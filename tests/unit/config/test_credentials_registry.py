#!/usr/bin/env python3
"""凭据注册表测试.

遵循项目单元测试设计规范:
- Mock外部依赖(环境变量), 保留内部业务逻辑
- 完全隔离, 毫秒级执行

测试重点:
- get_credential: 读取凭据值
- has_credential: 检查凭据是否已配置
- 注册表完整性: 工具/服务凭据环境变量映射
"""

from __future__ import annotations

import pytest

from src.config.credentials_registry import (
    CREDENTIALS,
    get_credential,
    has_credential,
    require_credential,
)


class TestCredentialsRegistry:
    """凭据注册表测试."""

    def test_all_credentials_registered(self) -> None:
        """验证全部凭据均已注册."""
        expected = {
            "file_signing_secret",
            "openclaw_gateway_token",
            "baidu_api_key",
            "zhipu_api_key",
            "ark_agent_plan_api_key",
            "baidu_maps_ak",
            "tencent_maps_key",
            "tencent_maps_sk",
            "smtp_host",
            "smtp_username",
            "smtp_password",
            "smtp_from_address",
        }
        assert set(CREDENTIALS.keys()) == expected

    def test_credential_spec_fields(self) -> None:
        """验证 CredentialSpec 字段完整性."""
        spec = CREDENTIALS["baidu_maps_ak"]
        assert spec.name == "baidu_maps_ak"
        assert spec.env_var == "BAIDU_MAPS_AK"
        assert spec.description  # 描述非空

    @pytest.mark.parametrize(
        ("name", "env_var"),
        [
            ("zhipu_api_key", "ZHIPU_API_KEY"),
            ("file_signing_secret", "FILE_SIGNING_SECRET"),
            ("openclaw_gateway_token", "OPENCLAW_GATEWAY_TOKEN"),
            ("baidu_api_key", "BAIDU_API_KEY"),
            ("ark_agent_plan_api_key", "ARK_AGENT_PLAN_API_KEY"),
            ("baidu_maps_ak", "BAIDU_MAPS_AK"),
            ("tencent_maps_key", "TENCENT_MAPS_KEY"),
            ("tencent_maps_sk", "TENCENT_MAPS_SK"),
            ("smtp_host", "SMTP_HOST"),
            ("smtp_username", "SMTP_USERNAME"),
            ("smtp_password", "SMTP_PASSWORD"),
            ("smtp_from_address", "SMTP_FROM_ADDRESS"),
        ],
    )
    def test_env_var_mapping(self, name: str, env_var: str) -> None:
        """验证凭据名到环境变量的映射."""
        assert CREDENTIALS[name].env_var == env_var

    def test_get_credential_returns_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_credential 返回环境变量值."""
        monkeypatch.setenv("ZHIPU_API_KEY", "test-key-123")
        assert get_credential("zhipu_api_key") == "test-key-123"

    def test_get_credential_unset_returns_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """未设置的环境变量返回空字符串."""
        monkeypatch.delenv("TENCENT_MAPS_SK", raising=False)
        assert get_credential("tencent_maps_sk") == ""

    def test_get_credential_unknown_name_raises(self) -> None:
        """未注册的凭据名抛 KeyError."""
        with pytest.raises(KeyError, match="未注册的凭据名称"):
            get_credential("nonexistent_key")

    def test_has_credential_true_when_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """has_credential 已配置返回 True."""
        monkeypatch.setenv("BAIDU_MAPS_AK", "ak-value")
        assert has_credential("baidu_maps_ak") is True

    def test_has_credential_false_when_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """has_credential 未配置返回 False."""
        monkeypatch.delenv("SMTP_FROM_ADDRESS", raising=False)
        assert has_credential("smtp_from_address") is False

    def test_has_credential_false_when_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """空字符串视为未配置."""
        monkeypatch.setenv("SMTP_PASSWORD", "")
        assert has_credential("smtp_password") is False

    def test_require_credential_raises_when_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """必填凭据缺失时抛 RuntimeError."""
        monkeypatch.delenv("FILE_SIGNING_SECRET", raising=False)
        with pytest.raises(RuntimeError, match="FILE_SIGNING_SECRET"):
            require_credential("file_signing_secret")


# 测试标记
pytestmark_unit = pytest.mark.unit
