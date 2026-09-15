"""ChannelPushClient 单元测试.

覆盖:
- send_message 成功/失败/网络异常/JSON 异常 (POST /channel/send 契约)
- 单例模式
- 配置解析优先级 (env > yaml > 默认)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.channel_push_client import (
    ChannelPushClient,
    _resolve_config,
    close_channel_push_client,
    get_channel_push_client,
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
        client = ChannelPushClient("http://localhost:18789", "test-token")
        client._client = MagicMock()
        client._client.post = AsyncMock(
            return_value=_make_response(200, {"ok": True}),
        )
        outcome = await client.send_message(
            account_id="bot-1",
            to="user-123@im.wechat",
            text="hello",
        )
        assert outcome.ok is True
        assert outcome.error is None
        client._client.post.assert_awaited_once()
        args, kwargs = client._client.post.call_args
        assert args[0] == "/channel/send"
        assert kwargs["json"] == {
            "channel": "weixin",
            "account_id": "bot-1",
            "to": "user-123@im.wechat",
            "text": "hello",
        }

    @pytest.mark.asyncio
    async def test_http_error_returns_false(self):
        client = ChannelPushClient("http://localhost:18789", "test-token")
        client._client = MagicMock()
        client._client.post = AsyncMock(
            return_value=_make_response(500, text="server error"),
        )
        outcome = await client.send_message("bot", "user", "x")
        assert outcome.ok is False
        assert "500" in outcome.error

    @pytest.mark.asyncio
    async def test_ok_false_returns_false(self):
        client = ChannelPushClient("http://localhost:18789", "test-token")
        client._client = MagicMock()
        client._client.post = AsyncMock(
            return_value=_make_response(200, {"ok": False, "error": "no route"}),
        )
        outcome = await client.send_message("bot", "user", "x")
        assert outcome.ok is False
        assert "no route" in outcome.error

    @pytest.mark.asyncio
    async def test_invalid_json_returns_false(self):
        client = ChannelPushClient("http://localhost:18789", "test-token")
        client._client = MagicMock()
        client._client.post = AsyncMock(
            return_value=_make_response(200),
        )
        outcome = await client.send_message("bot", "user", "x")
        assert outcome.ok is False
        assert outcome.error

    @pytest.mark.asyncio
    async def test_network_error_returns_false(self):
        import httpx

        client = ChannelPushClient("http://localhost:18789", "test-token")
        client._client = MagicMock()
        client._client.post = AsyncMock(side_effect=httpx.HTTPError("boom"))
        outcome = await client.send_message("bot", "user", "x")
        assert outcome.ok is False
        assert "boom" in outcome.error

    @pytest.mark.asyncio
    async def test_long_target_truncated_in_logs(self, caplog):
        client = ChannelPushClient("http://localhost:18789", "test-token")
        client._client = MagicMock()
        client._client.post = AsyncMock(
            return_value=_make_response(200, {"ok": True}),
        )
        long_target = "u" * 100
        with caplog.at_level("INFO"):
            await client.send_message("bot", long_target, "x")
        assert "u" * 20 not in caplog.text, "日志不应泄露完整 target"


class TestSingleton:
    """单例与生命周期测试."""

    @pytest.mark.asyncio
    async def test_get_client_returns_singleton(self, monkeypatch):
        monkeypatch.setattr(
            "src.core.channel_push_client._resolve_config",
            lambda: ("http://localhost:18789", "test-token"),
        )
        c1 = get_channel_push_client()
        c2 = get_channel_push_client()
        assert c1 is c2
        await close_channel_push_client()

    @pytest.mark.asyncio
    async def test_close_resets_singleton(self, monkeypatch):
        monkeypatch.setattr(
            "src.core.channel_push_client._resolve_config",
            lambda: ("http://localhost:18789", "test-token"),
        )
        c1 = get_channel_push_client()
        await close_channel_push_client()
        c2 = get_channel_push_client()
        assert c1 is not c2
        await close_channel_push_client()


class TestResolveConfig:
    """配置解析优先级测试."""

    def test_env_priority(self, monkeypatch):
        monkeypatch.setenv("CHANNEL_GATEWAY_URL", "http://env:9999")
        monkeypatch.setattr(
            "src.config.channel_push_config.get_config",
            lambda: MagicMock(
                gateway=MagicMock(url="http://yaml:18789"),
            ),
        )
        url, _ = _resolve_config()
        assert url == "http://env:9999"

    def test_default_when_no_config(self, monkeypatch):
        monkeypatch.delenv("CHANNEL_GATEWAY_URL", raising=False)
        with patch(
            "src.config.channel_push_config.get_config",
            side_effect=RuntimeError("no config"),
        ):
            url, _ = _resolve_config()
        assert url == "http://127.0.0.1:18789"

    def test_yaml_config_flows_to_client(self, monkeypatch):
        monkeypatch.delenv("CHANNEL_GATEWAY_URL", raising=False)
        monkeypatch.setattr(
            "src.config.channel_push_config.get_config",
            lambda: MagicMock(
                gateway=MagicMock(url="http://yaml:18789"),
            ),
        )
        url, _ = _resolve_config()
        assert url == "http://yaml:18789"
