"""运行时环境白名单测试."""

from __future__ import annotations

import pytest

from src.config import runtime_env


def test_runtime_env_bool_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEBUG", "true")
    assert runtime_env.is_debug_enabled() is True

    monkeypatch.setenv("DEBUG", "invalid")
    assert runtime_env.is_debug_enabled() is False


def test_runtime_env_rejects_unregistered_key() -> None:
    with pytest.raises(KeyError, match="未登记"):
        runtime_env.get_str("UNREGISTERED_ENV")


def test_tool_runtime_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOOL_RUNTIME_BASE_URL", raising=False)
    assert runtime_env.get_tool_runtime_base_url() == "http://127.0.0.1:8766"


def test_api_port_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_PORT", "9001")
    assert runtime_env.get_api_port_override() == 9001

    monkeypatch.setenv("API_PORT", "bad")
    with pytest.raises(ValueError, match="API_PORT"):
        runtime_env.get_api_port_override()
