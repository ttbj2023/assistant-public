"""chat.py 中 _auto_provision_openclaw_channel 测试.

覆盖:
- _auto_provision_openclaw_channel 基于 Inbound Context 的自动配置流程
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.chat import _auto_provision_openclaw_channel
from src.core.openclaw_filter import OpenClawInboundContext


def _make_ctx(
    account_id: str | None = "bot-1",
    channel: str | None = "weixin",
    chat_id: str | None = "u1",
) -> OpenClawInboundContext:
    return OpenClawInboundContext(
        account_id=account_id,
        channel=channel,
        chat_id=chat_id,
    )


def _make_request(
    ctx: OpenClawInboundContext | None = _make_ctx(),
    thread_id: str | None = "main",
) -> MagicMock:
    req = MagicMock()
    req.state.openclaw_context = ctx
    req.state.thread_id = thread_id
    return req


class TestAutoProvision:
    """_auto_provision_openclaw_channel 端到端流程测试."""

    @pytest.mark.asyncio
    async def test_skip_when_not_openclaw(self):
        """非 openclaw 请求 (ctx=None) 立即返回."""
        req = _make_request(ctx=None)
        result = await _auto_provision_openclaw_channel(req, "user-1", "personal-assistant")
        assert result is None

    @pytest.mark.asyncio
    async def test_skip_when_chat_id_missing(self):
        """chat_id 缺失时跳过."""
        req = _make_request(ctx=_make_ctx(chat_id=None))
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
        ) as mock:
            await _auto_provision_openclaw_channel(req, "user-1", "personal-assistant")
            mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skip_when_channel_missing(self):
        """channel 缺失时跳过."""
        req = _make_request(ctx=_make_ctx(channel=None))
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
        ) as mock:
            await _auto_provision_openclaw_channel(req, "user-1", "personal-assistant")
            mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skip_when_thread_id_missing(self):
        """thread_id 缺失时跳过."""
        req = _make_request(thread_id=None)
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
        ) as mock:
            await _auto_provision_openclaw_channel(req, "user-1", "personal-assistant")
            mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skip_when_existing_has_account(self):
        """数据库已有 account 时跳过."""
        req = _make_request()
        existing = {
            "target": "u1",
            "openclaw_channel_key": "weixin",
            "openclaw_account": "bot-existing",
        }
        mock_service = MagicMock()
        mock_service.get_config_for_channel = AsyncMock(return_value=existing)
        mock_service.upsert_channel_config = AsyncMock()

        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            AsyncMock(return_value=mock_service),
        ):
            await _auto_provision_openclaw_channel(req, "user-1", "personal-assistant")
            mock_service.upsert_channel_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fill_account_when_empty(self):
        """数据库 account 为空, 从 Inbound Context 提取 account_id 并写入."""
        req = _make_request(ctx=_make_ctx(account_id="bot-from-ctx"))
        existing = {
            "target": "u1",
            "openclaw_channel_key": "weixin",
            "openclaw_account": "",
        }
        mock_service = MagicMock()
        mock_service.get_config_for_channel = AsyncMock(return_value=existing)
        mock_service.upsert_channel_config = AsyncMock()

        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            AsyncMock(return_value=mock_service),
        ):
            await _auto_provision_openclaw_channel(req, "user-1", "personal-assistant")

        mock_service.upsert_channel_config.assert_awaited_once()
        kwargs = mock_service.upsert_channel_config.await_args.kwargs
        assert kwargs["config"]["openclaw_account"] == "bot-from-ctx"
        assert kwargs["config"]["target"] == "u1"

    @pytest.mark.asyncio
    async def test_create_new_when_no_existing(self):
        """数据库无任何配置, 创建新记录."""
        req = _make_request(ctx=_make_ctx(account_id="bot-new"))
        mock_service = MagicMock()
        mock_service.get_config_for_channel = AsyncMock(return_value=None)
        mock_service.upsert_channel_config = AsyncMock()

        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            AsyncMock(return_value=mock_service),
        ):
            await _auto_provision_openclaw_channel(req, "user-1", "personal-assistant")

        mock_service.upsert_channel_config.assert_awaited_once()
        kwargs = mock_service.upsert_channel_config.await_args.kwargs
        assert kwargs["config"] == {
            "target": "u1",
            "openclaw_channel_key": "weixin",
            "openclaw_account": "bot-new",
        }

    @pytest.mark.asyncio
    async def test_passes_thread_id_and_agent_id(self):
        """确保 (user_id, thread_id, agent_id) 三参正确传递给 config service."""
        req = _make_request(ctx=_make_ctx(account_id="bot-new"))
        mock_service = MagicMock()
        mock_service.get_config_for_channel = AsyncMock(return_value=None)
        mock_service.upsert_channel_config = AsyncMock()

        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            AsyncMock(return_value=mock_service),
        ) as mock_factory:
            await _auto_provision_openclaw_channel(req, "user-1", "personal-assistant")

        mock_factory.assert_awaited_once_with("user-1", "main", "personal-assistant")

    @pytest.mark.asyncio
    async def test_exception_is_swallowed(self, caplog):
        """内部异常不应抛出 (非阻塞)."""
        import logging

        req = _make_request()
        with caplog.at_level(logging.WARNING):
            with patch(
                "src.storage.service.user_channel_config_service.get_user_channel_config_service",
                AsyncMock(side_effect=RuntimeError("db down")),
            ):
                result = await _auto_provision_openclaw_channel(
                    req, "user-1", "personal-assistant"
                )
        assert result is None
        assert "自动写入OpenClaw渠道配置失败" in caplog.text
