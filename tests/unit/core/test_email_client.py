"""EmailClient 单元测试.

覆盖:
- send_email 成功(TLS/start_tls)/配置不完整/收件人空/html multipart/发送异常
- 单例模式
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.config.smtp_config import ResolvedSmtpCredentials
from src.core import email_client as mod
from src.core.email_client import EmailClient, close_email_client, get_email_client


def _creds(complete: bool = True) -> ResolvedSmtpCredentials:
    return ResolvedSmtpCredentials(
        host="smtp.test" if complete else "",
        port=465,
        use_tls=True,
        username="u@t" if complete else "",
        password="p" if complete else "",
        from_address="u@t",
    )


class TestSendEmail:
    @pytest.mark.asyncio
    async def test_success_tls(self):
        with (
            patch("src.core.email_client.resolve_credentials", return_value=_creds()),
            patch("aiosmtplib.send", new=AsyncMock()) as mock_send,
        ):
            ok = await EmailClient().send_email("to@x.com", "主题", "正文")
        assert ok is True
        mock_send.assert_awaited_once()
        assert mock_send.call_args.kwargs["hostname"] == "smtp.test"
        assert mock_send.call_args.kwargs["use_tls"] is True

    @pytest.mark.asyncio
    async def test_success_starttls(self):
        creds = ResolvedSmtpCredentials(
            host="smtp.test", port=587, use_tls=False, username="u", password="p", from_address="u"
        )
        with (
            patch("src.core.email_client.resolve_credentials", return_value=creds),
            patch("aiosmtplib.send", new=AsyncMock()) as mock_send,
        ):
            ok = await EmailClient().send_email("to@x.com", "s", "b")
        assert ok is True
        assert mock_send.call_args.kwargs["start_tls"] is True

    @pytest.mark.asyncio
    async def test_html_multipart(self):
        with (
            patch("src.core.email_client.resolve_credentials", return_value=_creds()),
            patch("aiosmtplib.send", new=AsyncMock()) as mock_send,
        ):
            ok = await EmailClient().send_email(
                "to@x.com", "s", "text body", html="<b>html</b>"
            )
        assert ok is True
        msg = mock_send.call_args.args[0]
        assert msg.get_content_type() == "multipart/alternative"

    @pytest.mark.asyncio
    async def test_smtp_unconfigured_returns_false(self):
        with patch(
            "src.core.email_client.resolve_credentials",
            return_value=_creds(complete=False),
        ):
            ok = await EmailClient().send_email("to@x.com", "s", "b")
        assert ok is False

    @pytest.mark.asyncio
    async def test_empty_recipient_returns_false(self):
        ok = await EmailClient().send_email("", "s", "b")
        assert ok is False

    @pytest.mark.asyncio
    async def test_send_exception_returns_false(self):
        with (
            patch("src.core.email_client.resolve_credentials", return_value=_creds()),
            patch(
                "aiosmtplib.send",
                new=AsyncMock(side_effect=ConnectionRefusedError("refused")),
            ),
        ):
            ok = await EmailClient().send_email("to@x.com", "s", "b")
        assert ok is False


class TestSingleton:
    @pytest.mark.asyncio
    async def test_get_client_returns_singleton(self):
        mod._client_instance = None
        try:
            c1 = get_email_client()
            c2 = get_email_client()
            assert c1 is c2
        finally:
            await close_email_client()
            mod._client_instance = None

    @pytest.mark.asyncio
    async def test_close_resets_singleton(self):
        mod._client_instance = None
        c1 = get_email_client()
        await close_email_client()
        assert mod._client_instance is None
        c2 = get_email_client()
        try:
            assert c1 is not c2
        finally:
            await close_email_client()
            mod._client_instance = None
