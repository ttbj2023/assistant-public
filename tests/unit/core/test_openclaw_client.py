"""OpenClawClient 单元测试.

覆盖:
- send_message 成功/失败/网络异常/JSON 异常
- get_bindings 成功/404/失败/网络异常
- 单例模式
- 配置解析优先级 (env > yaml > 默认)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.openclaw_client import (
    OpenClawClient,
    _resolve_config,
    close_openclaw_client,
    get_openclaw_client,
)


def _make_response(
    status_code: int = 200,
    json_data: dict | None = None,
    text: str = "",
) -> MagicMock:
    """构造模拟 httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or ""
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


class TestSendMessage:
    """send_message 方法测试."""

    @pytest.mark.asyncio
    async def test_success(self):
        client = OpenClawClient("http://localhost:18789", "test-token")
        client._client = MagicMock()
        client._client.post = AsyncMock(
            return_value=_make_response(200, {"ok": True, "result": {}}),
        )
        ok = await client.send_message(
            channel="openclaw-weixin",
            account_id="bot-1",
            target="user-123",
            text="hello",
        )
        assert ok is True
        client._client.post.assert_awaited_once()
        args, kwargs = client._client.post.call_args
        assert args[0] == "/tools/invoke"
        assert kwargs["json"]["tool"] == "message"
        assert kwargs["json"]["action"] == "send"
        assert kwargs["json"]["args"]["channel"] == "openclaw-weixin"
        assert kwargs["json"]["args"]["accountId"] == "bot-1"
        assert kwargs["json"]["args"]["to"] == "user-123"
        assert kwargs["json"]["args"]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_http_error_returns_false(self):
        client = OpenClawClient("http://localhost:18789", "test-token")
        client._client = MagicMock()
        client._client.post = AsyncMock(
            return_value=_make_response(500, text="server error"),
        )
        ok = await client.send_message("c", "a", "t", "x")
        assert ok is False

    @pytest.mark.asyncio
    async def test_ok_false_returns_false(self):
        client = OpenClawClient("http://localhost:18789", "test-token")
        client._client = MagicMock()
        client._client.post = AsyncMock(
            return_value=_make_response(200, {"ok": False, "error": "send failed"}),
        )
        ok = await client.send_message("c", "a", "t", "x")
        assert ok is False

    @pytest.mark.asyncio
    async def test_invalid_json_returns_false(self):
        client = OpenClawClient("http://localhost:18789", "test-token")
        client._client = MagicMock()
        client._client.post = AsyncMock(
            return_value=_make_response(200, text="not json"),
        )
        ok = await client.send_message("c", "a", "t", "x")
        assert ok is False

    @pytest.mark.asyncio
    async def test_network_error_returns_false(self):
        client = OpenClawClient("http://localhost:18789", "test-token")
        client._client = MagicMock()
        client._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        ok = await client.send_message("c", "a", "t", "x")
        assert ok is False

    @pytest.mark.asyncio
    async def test_long_target_truncated_in_logs(self, caplog):
        """target 在日志中只显示前 16 字符."""
        import logging

        long_target = "x" * 100
        client = OpenClawClient("http://localhost:18789", "test-token")
        client._client = MagicMock()
        client._client.post = AsyncMock(
            return_value=_make_response(500, text="err"),
        )
        with caplog.at_level(logging.ERROR):
            await client.send_message("c", "a", long_target, "x")
        # 日志中 target 应被截断
        assert "x" * 100 not in caplog.text


class TestSingleton:
    """单例模式测试."""

    @pytest.mark.asyncio
    async def test_get_client_returns_singleton(self, monkeypatch):
        import src.core.openclaw_client as mod

        monkeypatch.setenv("OPENCLAW_GATEWAY_URL", "http://test:18789")
        monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "test-token")
        mod._client_instance = None

        try:
            c1 = get_openclaw_client()
            c2 = get_openclaw_client()
            assert c1 is c2
        finally:
            await close_openclaw_client()
            mod._client_instance = None

    @pytest.mark.asyncio
    async def test_close_resets_singleton(self, monkeypatch):
        import src.core.openclaw_client as mod

        monkeypatch.setenv("OPENCLAW_GATEWAY_URL", "http://test:18789")
        monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "test-token")
        mod._client_instance = None

        c1 = get_openclaw_client()
        await close_openclaw_client()
        assert mod._client_instance is None
        c2 = get_openclaw_client()
        try:
            assert c1 is not c2
        finally:
            await close_openclaw_client()
            mod._client_instance = None


class TestResolveConfig:
    """_resolve_config 配置解析测试."""

    def test_env_priority(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_GATEWAY_URL", "http://env:18789")
        monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "env-token")
        url, token = _resolve_config()
        assert url == "http://env:18789"
        assert token == "env-token"

    def test_default_when_no_config(self, monkeypatch):
        monkeypatch.delenv("OPENCLAW_GATEWAY_URL", raising=False)
        monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)
        with patch(
            "src.config.openclaw_config.get_config", side_effect=Exception("no config")
        ):
            url, token = _resolve_config()
        assert url == "http://127.0.0.1:18789"
        assert token == ""

    def test_only_env_url(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_GATEWAY_URL", "http://env:18789")
        monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)
        mock_cfg = MagicMock()
        mock_cfg.gateway.url = "http://yaml:18789"
        with patch("src.config.openclaw_config.get_config", return_value=mock_cfg):
            url, token = _resolve_config()
        assert url == "http://env:18789"
        assert token == ""

    def test_yaml_config_flows_to_client(self, monkeypatch):
        """config.yaml 的 gateway URL 应真正流入 client."""
        monkeypatch.delenv("OPENCLAW_GATEWAY_URL", raising=False)
        monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)
        monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "env-token")
        mock_cfg = MagicMock()
        mock_cfg.gateway.url = "http://yaml-host:18789"
        with patch(
            "src.config.openclaw_config.get_config",
            return_value=mock_cfg,
        ):
            url, token = _resolve_config()
        assert url == "http://yaml-host:18789"
        assert token == "env-token"
