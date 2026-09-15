"""NotificationService / resolve_delivery 单元测试.

覆盖:
- send 按 method 分流到 ChannelPushClient(wechat) / EmailClient(email)
- send 未知 method 返回 False
- resolve_delivery 解析 wechat / email / 配置不完整 / 无配置 / 异常
- 单例模式
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core import notification as mod
from src.core.channel_push_client import SendOutcome
from src.core.notification import (
    DeliverySpec,
    NotificationService,
    close_notification_service,
    get_notification_service,
    resolve_delivery,
)


class TestSend:
    """NotificationService.send 分流测试."""

    @pytest.mark.asyncio
    async def test_wechat_dispatches_to_channel_push(self):
        mock_cp = MagicMock()
        mock_cp.send_message = AsyncMock(return_value=SendOutcome(ok=True))
        with patch(
            "src.core.notification.get_channel_push_client", return_value=mock_cp
        ):
            delivery = DeliverySpec(
                method="wechat",
                account_id="a1",
                target="t1",
            )
            outcome = await NotificationService().send(delivery, "hello")
        assert outcome.ok is True
        assert outcome.error is None
        mock_cp.send_message.assert_awaited_once()
        kwargs = mock_cp.send_message.call_args.kwargs
        assert kwargs["account_id"] == "a1"
        assert kwargs["to"] == "t1"
        assert kwargs["text"] == "hello"
        assert kwargs["method"] == "wechat"

    @pytest.mark.asyncio
    async def test_wechat_error_passthrough(self):
        mock_cp = MagicMock()
        mock_cp.send_message = AsyncMock(
            return_value=SendOutcome(ok=False, error="网关投递失败: prepare failed")
        )
        with patch(
            "src.core.notification.get_channel_push_client", return_value=mock_cp
        ):
            delivery = DeliverySpec(method="wechat", account_id="a1", target="t1")
            outcome = await NotificationService().send(delivery, "hello")
        assert outcome.ok is False
        assert "prepare failed" in outcome.error

    @pytest.mark.asyncio
    async def test_email_dispatches_to_email_client(self):
        mock_ec = MagicMock()
        mock_ec.send_email = AsyncMock(return_value=True)
        with patch("src.core.notification.get_email_client", return_value=mock_ec):
            delivery = DeliverySpec(method="email", email_address="to@x.com")
            outcome = await NotificationService().send(
                delivery, "正文", subject="主题", html="<b>h</b>"
            )
        assert outcome.ok is True
        kwargs = mock_ec.send_email.call_args.kwargs
        assert kwargs["to"] == "to@x.com"
        assert kwargs["subject"] == "主题"
        assert kwargs["body"] == "正文"
        assert kwargs["html"] == "<b>h</b>"

    @pytest.mark.asyncio
    async def test_email_failure_carries_error(self):
        mock_ec = MagicMock()
        mock_ec.send_email = AsyncMock(return_value=False)
        with patch("src.core.notification.get_email_client", return_value=mock_ec):
            delivery = DeliverySpec(method="email", email_address="to@x.com")
            outcome = await NotificationService().send(delivery, "正文")
        assert outcome.ok is False
        assert outcome.error

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
        outcome = await NotificationService().send(delivery, "x")
        assert outcome.ok is False
        assert outcome.error


class TestResolveDelivery:
    """resolve_delivery 配置解析测试."""

    @pytest.mark.asyncio
    async def test_wechat_success(self):
        cfg = {"target": "t1", "account_id": "a1"}
        mock_svc = MagicMock()
        mock_svc.get_config_for_channel = AsyncMock(return_value=cfg)
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            new=AsyncMock(return_value=mock_svc),
        ):
            delivery = await resolve_delivery("u", "t", "a", "wechat")
        assert delivery is not None
        assert delivery.method == "wechat"
        assert delivery.target == "t1"
        assert delivery.account_id == "a1"

    @pytest.mark.asyncio
    async def test_wechat_missing_account_returns_none(self):
        cfg = {"target": "t1"}  # 缺 account_id
        mock_svc = MagicMock()
        mock_svc.get_config_for_channel = AsyncMock(return_value=cfg)
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            new=AsyncMock(return_value=mock_svc),
        ):
            delivery = await resolve_delivery("u", "t", "a", "wechat")
        assert delivery is None

    @pytest.mark.asyncio
    async def test_wechat_missing_target_returns_none(self):
        cfg = {"account_id": "a1"}  # 缺 target
        mock_svc = MagicMock()
        mock_svc.get_config_for_channel = AsyncMock(return_value=cfg)
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            new=AsyncMock(return_value=mock_svc),
        ):
            delivery = await resolve_delivery("u", "t", "a", "wechat")
        assert delivery is None

    @pytest.mark.asyncio
    async def test_wechat_legacy_openclaw_fields_ignored(self):
        """旧 openclaw_account 字段不再被读取 (改名后存量配置作废, 自愈重写)."""
        cfg = {"target": "t1", "openclaw_account": "a1"}
        mock_svc = MagicMock()
        mock_svc.get_config_for_channel = AsyncMock(return_value=cfg)
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            new=AsyncMock(return_value=mock_svc),
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
