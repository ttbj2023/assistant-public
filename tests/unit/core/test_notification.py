"""NotificationService / resolve_delivery 单元测试.

覆盖:
- send 按 method 分流到 OpenClawClient(wechat) / EmailClient(email)
- send 未知 method 返回 False
- resolve_delivery 解析 wechat / email / 配置不完整 / 无配置 / 异常
- _resolve_openclaw_channel 从配置读取渠道名
- 单例模式
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core import notification as mod
from src.core.notification import (
    DeliverySpec,
    NotificationService,
    _resolve_openclaw_channel,
    close_notification_service,
    get_notification_service,
    resolve_delivery,
)


class TestSend:
    """NotificationService.send 分流测试."""

    @pytest.mark.asyncio
    async def test_wechat_dispatches_to_openclaw(self):
        mock_oc = MagicMock()
        mock_oc.send_message = AsyncMock(return_value=True)
        with patch("src.core.notification.get_openclaw_client", return_value=mock_oc):
            delivery = DeliverySpec(
                method="wechat",
                openclaw_channel="openclaw-weixin",
                account_id="a1",
                target="t1",
            )
            ok = await NotificationService().send(delivery, "hello")
        assert ok is True
        mock_oc.send_message.assert_awaited_once()
        kwargs = mock_oc.send_message.call_args.kwargs
        assert kwargs["channel"] == "openclaw-weixin"
        assert kwargs["account_id"] == "a1"
        assert kwargs["target"] == "t1"
        assert kwargs["text"] == "hello"

    @pytest.mark.asyncio
    async def test_email_dispatches_to_email_client(self):
        mock_ec = MagicMock()
        mock_ec.send_email = AsyncMock(return_value=True)
        with patch("src.core.notification.get_email_client", return_value=mock_ec):
            delivery = DeliverySpec(method="email", email_address="to@x.com")
            ok = await NotificationService().send(
                delivery, "正文", subject="主题", html="<b>h</b>"
            )
        assert ok is True
        kwargs = mock_ec.send_email.call_args.kwargs
        assert kwargs["to"] == "to@x.com"
        assert kwargs["subject"] == "主题"
        assert kwargs["body"] == "正文"
        assert kwargs["html"] == "<b>h</b>"

    @pytest.mark.asyncio
    async def test_email_default_subject_when_empty(self):
        mock_ec = MagicMock()
        mock_ec.send_email = AsyncMock(return_value=True)
        with patch("src.core.notification.get_email_client", return_value=mock_ec):
            delivery = DeliverySpec(method="email", email_address="to@x.com")
            await NotificationService().send(delivery, "正文")
        assert mock_ec.send_email.call_args.kwargs["subject"] == "通知"

    @pytest.mark.asyncio
    async def test_unknown_method_returns_false(self):
        delivery = DeliverySpec(method="sms")
        ok = await NotificationService().send(delivery, "x")
        assert ok is False


class TestResolveDelivery:
    """resolve_delivery 配置解析测试."""

    @pytest.mark.asyncio
    async def test_wechat_success(self):
        cfg = {"target": "t1", "openclaw_account": "a1", "openclaw_channel_key": "weixin"}
        mock_svc = MagicMock()
        mock_svc.get_config_for_channel = AsyncMock(return_value=cfg)
        with (
            patch(
                "src.storage.service.user_channel_config_service.get_user_channel_config_service",
                new=AsyncMock(return_value=mock_svc),
            ),
            patch(
                "src.core.notification._resolve_openclaw_channel",
                return_value="openclaw-weixin",
            ),
        ):
            delivery = await resolve_delivery("u", "t", "a", "wechat")
        assert delivery is not None
        assert delivery.method == "wechat"
        assert delivery.target == "t1"
        assert delivery.account_id == "a1"
        assert delivery.openclaw_channel == "openclaw-weixin"

    @pytest.mark.asyncio
    async def test_wechat_missing_account_returns_none(self):
        cfg = {"target": "t1"}  # 缺 openclaw_account
        mock_svc = MagicMock()
        mock_svc.get_config_for_channel = AsyncMock(return_value=cfg)
        with (
            patch(
                "src.storage.service.user_channel_config_service.get_user_channel_config_service",
                new=AsyncMock(return_value=mock_svc),
            ),
            patch("src.core.notification._resolve_openclaw_channel", return_value="openclaw-weixin"),
        ):
            delivery = await resolve_delivery("u", "t", "a", "wechat")
        assert delivery is None

    @pytest.mark.asyncio
    async def test_wechat_no_system_channel_returns_none(self):
        cfg = {"target": "t1", "openclaw_account": "a1"}
        mock_svc = MagicMock()
        mock_svc.get_config_for_channel = AsyncMock(return_value=cfg)
        with (
            patch(
                "src.storage.service.user_channel_config_service.get_user_channel_config_service",
                new=AsyncMock(return_value=mock_svc),
            ),
            patch("src.core.notification._resolve_openclaw_channel", return_value=""),
        ):
            delivery = await resolve_delivery("u", "t", "a", "wechat")
        assert delivery is None

    @pytest.mark.asyncio
    async def test_email_success(self):
        cfg = {"email_address": "to@x.com"}
        mock_svc = MagicMock()
        mock_svc.get_config_for_channel = AsyncMock(return_value=cfg)
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            new=AsyncMock(return_value=mock_svc),
        ):
            delivery = await resolve_delivery("u", "t", "a", "email")
        assert delivery is not None
        assert delivery.method == "email"
        assert delivery.email_address == "to@x.com"

    @pytest.mark.asyncio
    async def test_email_missing_address_returns_none(self):
        cfg = {}
        mock_svc = MagicMock()
        mock_svc.get_config_for_channel = AsyncMock(return_value=cfg)
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            new=AsyncMock(return_value=mock_svc),
        ):
            delivery = await resolve_delivery("u", "t", "a", "email")
        assert delivery is None

    @pytest.mark.asyncio
    async def test_no_config_returns_none(self):
        mock_svc = MagicMock()
        mock_svc.get_config_for_channel = AsyncMock(return_value=None)
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            new=AsyncMock(return_value=mock_svc),
        ):
            delivery = await resolve_delivery("u", "t", "a", "wechat")
        assert delivery is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            delivery = await resolve_delivery("u", "t", "a", "wechat")
        assert delivery is None

    @pytest.mark.asyncio
    async def test_unknown_channel_returns_none(self):
        mock_svc = MagicMock()
        mock_svc.get_config_for_channel = AsyncMock(return_value={"x": "y"})
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            new=AsyncMock(return_value=mock_svc),
        ):
            delivery = await resolve_delivery("u", "t", "a", "sms")
        assert delivery is None


class TestResolveOpenclawChannel:
    """_resolve_openclaw_channel 配置读取测试."""

    def test_returns_channel_for_known_key(self):
        from src.config.openclaw_config import OpenClawNotificationDefaults

        mock_cfg = MagicMock()
        mock_cfg.notification_defaults = {
            "weixin": OpenClawNotificationDefaults(channel="openclaw-weixin"),
        }
        with patch("src.config.openclaw_config.get_config", return_value=mock_cfg):
            assert _resolve_openclaw_channel("weixin") == "openclaw-weixin"

    def test_missing_key_returns_empty(self):
        mock_cfg = MagicMock()
        mock_cfg.notification_defaults = {}
        with patch("src.config.openclaw_config.get_config", return_value=mock_cfg):
            assert _resolve_openclaw_channel("sms") == ""


class TestSingleton:
    @pytest.mark.asyncio
    async def test_get_service_returns_singleton(self):
        mod._service_instance = None
        try:
            s1 = get_notification_service()
            s2 = get_notification_service()
            assert s1 is s2
        finally:
            await close_notification_service()
            mod._service_instance = None

    @pytest.mark.asyncio
    async def test_close_resets_singleton(self):
        mod._service_instance = None
        s1 = get_notification_service()
        await close_notification_service()
        assert mod._service_instance is None
        s2 = get_notification_service()
        try:
            assert s1 is not s2
        finally:
            await close_notification_service()
            mod._service_instance = None
