"""GraphAuthService 单元测试.

Mock 策略: httpx.MockTransport 注入 (devicecode/token 两端点), token 落盘经
base_path 指向 tmp_path; 后台轮询任务经 service 持有的 task 引用 await 同步.
"""

from __future__ import annotations

import httpx

from src.sync.graph_auth_service import GraphAuthService
from src.sync.token_store import GraphTokenStore

_DEVICE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"

_DEVICE_RESP = {
    "verification_uri": "https://microsoft.com/devicelogin",
    "user_code": "ABC123",
    "device_code": "dc-1",
    "expires_in": 900,
    "interval": 0.001,
}

_TOKEN_RESP = {
    "token_type": "Bearer",
    "scope": "offline_access Tasks.ReadWrite",
    "expires_in": 3600,
    "access_token": "at-new",
    "refresh_token": "rt-new",
}


def _transport(token_responses: list[dict | None]) -> httpx.MockTransport:
    """token 端点按序出队响应; dict=成功, None=取下一个 pending."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == _DEVICE_URL:
            return httpx.Response(200, json=_DEVICE_RESP, request=request)
        if url == _TOKEN_URL:
            resp = token_responses.pop(0) if token_responses else _TOKEN_RESP
            if resp is None:
                return httpx.Response(
                    200,
                    json={"error": "authorization_pending"},
                    request=request,
                )
            return httpx.Response(200, json=resp, request=request)
        return httpx.Response(500, json={"error": "unexpected"}, request=request)

    return httpx.MockTransport(handler)


def _service(
    tmp_path, token_responses: list[dict | None] | None = None
) -> tuple[GraphAuthService, list[dict | None]]:
    responses: list[dict | None] = (
        token_responses if token_responses is not None else []
    )
    service = GraphAuthService(
        http=httpx.AsyncClient(transport=_transport(responses)),
        base_path=tmp_path,
    )
    return service, responses


class TestStartAuthorization:
    """start_authorization 测试."""

    async def test_start_authorization_发起成功_返回验证信息且状态pending(
        self, tmp_path
    ):
        service, _ = _service(tmp_path)

        flow = await service.start_authorization("test_user")

        assert flow["verification_uri"] == "https://microsoft.com/devicelogin"
        assert flow["user_code"] == "ABC123"
        assert flow["expires_in"] == 900
        status = service.get_status("test_user")
        assert status["status"] == "pending"
        assert status["user_code"] == "ABC123"

        await service.shutdown()

    async def test_start_authorization_轮询完成_token落盘状态authorized(self, tmp_path):
        service, _ = _service(tmp_path)

        await service.start_authorization("test_user")
        await service._tasks["test_user"]

        store = GraphTokenStore("test_user", base_path=tmp_path)
        assert store.exists()
        assert store.load()["access_token"] == "at-new"
        assert service.get_status("test_user")["status"] == "authorized"

        await service.shutdown()

    async def test_start_authorization_授权被拒绝_状态failed带原因(self, tmp_path):
        service, responses = _service(
            tmp_path,
            token_responses=[{"error": "authorization_declined"}],
        )

        await service.start_authorization("test_user")
        await service._tasks["test_user"]

        status = service.get_status("test_user")
        assert status["status"] == "failed"
        assert "authorization_declined" in status["error"]

        await service.shutdown()

    async def test_start_authorization_已授权用户_拒绝重复发起(self, tmp_path):
        await GraphTokenStore("test_user", base_path=tmp_path).save(
            {"access_token": "at", "refresh_token": "rt"},
        )
        service, _ = _service(tmp_path)

        import pytest

        with pytest.raises(RuntimeError, match="已授权"):
            await service.start_authorization("test_user")

        await service.shutdown()


class TestStatusAndRevoke:
    """get_status / revoke 测试."""

    async def test_get_status_无token无pending_返回none(self, tmp_path):
        service, _ = _service(tmp_path)

        assert service.get_status("test_user") == {"status": "none"}

        await service.shutdown()

    async def test_revoke_已授权用户_删除token返回True(self, tmp_path):
        store = GraphTokenStore("test_user", base_path=tmp_path)
        await store.save({"access_token": "at", "refresh_token": "rt"})
        service, _ = _service(tmp_path)

        assert service.revoke("test_user") is True
        assert store.exists() is False
        assert service.get_status("test_user")["status"] == "none"

        await service.shutdown()

    async def test_revoke_未授权用户_返回False(self, tmp_path):
        service, _ = _service(tmp_path)

        assert service.revoke("test_user") is False

        await service.shutdown()

    async def test_revoke_pending用户_取消后台任务并清状态(self, tmp_path):
        service, responses = _service(tmp_path, token_responses=[None] * 100)
        await service.start_authorization("test_user")

        assert service.revoke("test_user") is True

        assert service.get_status("test_user")["status"] == "none"
        await service.shutdown()


class TestAuthCompletionCallback:
    """授权完成回调测试 (唤醒引擎 + 微信渠道自动回执)."""

    async def test_授权成功_唤醒同步引擎(self, tmp_path):
        from unittest.mock import patch

        service, _ = _service(tmp_path)

        with patch(
            "src.sync.graph_auth_service.notify_local_write",
        ) as mock_wake:
            await service.start_authorization(
                "test_user",
                thread_id="t1",
                agent_id="a1",
            )
            await service._tasks["test_user"]

        mock_wake.assert_called_once_with("test_user")

        await service.shutdown()

    async def test_授权成功_有微信渠道配置_推送回执(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch

        service, _ = _service(tmp_path)
        config_service = AsyncMock()
        config_service.get_config_for_channel = AsyncMock(
            return_value={"target": "wx:u1", "account_id": "bot-1"},
        )
        notification = AsyncMock()
        notification.send = AsyncMock(
            return_value=MagicMock(ok=True, error=None),
        )

        with (
            patch(
                "src.sync.graph_auth_service.notify_local_write",
            ),
            patch(
                "src.storage.service.user_channel_config_service"
                ".get_user_channel_config_service",
                AsyncMock(return_value=config_service),
            ),
            patch(
                "src.core.notification.get_notification_service",
                return_value=notification,
            ),
        ):
            await service.start_authorization(
                "test_user",
                thread_id="t1",
                agent_id="personal-assistant",
            )
            await service._tasks["test_user"]

        spec = notification.send.call_args.args[0]
        assert spec.method == "wechat"
        assert spec.account_id == "bot-1"
        assert spec.target == "wx:u1"
        text = notification.send.call_args.args[1]
        assert "授权成功" in text

        await service.shutdown()

    async def test_授权成功_无微信配置_不发送不抛错(self, tmp_path):
        from unittest.mock import AsyncMock, patch

        service, _ = _service(tmp_path)
        config_service = AsyncMock()
        config_service.get_config_for_channel = AsyncMock(return_value=None)

        with (
            patch(
                "src.sync.graph_auth_service.notify_local_write",
            ),
            patch(
                "src.storage.service.user_channel_config_service"
                ".get_user_channel_config_service",
                AsyncMock(return_value=config_service),
            ),
            patch(
                "src.core.notification.get_notification_service",
            ) as mock_get_ns,
        ):
            await service.start_authorization(
                "test_user",
                thread_id="t1",
                agent_id="a1",
            )
            await service._tasks["test_user"]

        mock_get_ns.assert_not_called()

        await service.shutdown()

    async def test_推送失败_不影响授权结果(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch

        service, _ = _service(tmp_path)
        config_service = AsyncMock()
        config_service.get_config_for_channel = AsyncMock(
            return_value={"target": "wx:u1", "account_id": "bot-1"},
        )
        notification = AsyncMock()
        notification.send = AsyncMock(
            return_value=MagicMock(ok=False, error="网关网络异常"),
        )

        with (
            patch(
                "src.sync.graph_auth_service.notify_local_write",
            ),
            patch(
                "src.storage.service.user_channel_config_service"
                ".get_user_channel_config_service",
                AsyncMock(return_value=config_service),
            ),
            patch(
                "src.core.notification.get_notification_service",
                return_value=notification,
            ),
        ):
            await service.start_authorization(
                "test_user",
                thread_id="t1",
                agent_id="a1",
            )
            await service._tasks["test_user"]

        assert service.get_status("test_user")["status"] == "authorized"

        await service.shutdown()

    async def test_无对话上下文_跳过发送但仍唤醒(self, tmp_path):
        from unittest.mock import patch

        service, _ = _service(tmp_path)

        with (
            patch(
                "src.sync.graph_auth_service.notify_local_write",
            ) as mock_wake,
            patch(
                "src.storage.service.user_channel_config_service"
                ".get_user_channel_config_service",
            ) as mock_get_cfg,
        ):
            await service.start_authorization("test_user")
            await service._tasks["test_user"]

        mock_wake.assert_called_once_with("test_user")
        mock_get_cfg.assert_not_called()

        await service.shutdown()
