"""MSGraphClient 单元测试.

Mock 策略: httpx.MockTransport 注入, 按 URL 路由到 devicecode / token / graph
三个端点, 不发真实 HTTP 请求; 401-重试用带状态的有状态 handler 模拟.
"""

from __future__ import annotations

import httpx
import pytest

from src.sync.msgraph_client import MSGraphAuthError, MSGraphClient

_DEVICE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_DEVICE_RESP = {
    "verification_uri": "https://microsoft.com/devicelogin",
    "user_code": "ABC123",
    "device_code": "dc-1",
    "expires_in": 900,
    "interval": 5,
}

_TOKEN_RESP = {
    "token_type": "Bearer",
    "scope": "offline_access Tasks.ReadWrite",
    "expires_in": 3600,
    "access_token": "at-new",
    "refresh_token": "rt-new",
}


def _json_response(url: str, payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("POST", url))


def _route_device_flow(state: dict) -> httpx.MockTransport:
    """devicecode 成功 + token 端点先 pending 后成功 (有状态)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == _DEVICE_URL:
            return _json_response(_DEVICE_URL, _DEVICE_RESP)
        body = request.content.decode()
        if "authorization_pending" in state.get("token_error_seq", []):
            state["token_error_seq"].pop(0)
            return _json_response(
                _TOKEN_URL,
                {"error": "authorization_pending"},
            )
        return _json_response(_TOKEN_URL, _TOKEN_RESP)

    return httpx.MockTransport(handler)


class TestStartDeviceFlow:
    """start_device_flow 测试."""

    async def test_start_device_flow_发起成功_返回验证地址与用户码(self):
        transport = _route_device_flow({})
        client = MSGraphClient(http=httpx.AsyncClient(transport=transport))

        flow = await client.start_device_flow()

        assert flow["verification_uri"] == "https://microsoft.com/devicelogin"
        assert flow["user_code"] == "ABC123"
        assert flow["device_code"] == "dc-1"

    async def test_start_device_flow_端点返回错误_抛出AuthError(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(_DEVICE_URL, {"error": "invalid_request"}, 400)

        client = MSGraphClient(
            http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

        with pytest.raises(MSGraphAuthError):
            await client.start_device_flow()


class TestPollDeviceFlow:
    """poll_device_flow 测试."""

    async def test_poll_device_flow_等待授权后完成_返回token字典(self):
        state = {"token_error_seq": ["authorization_pending"]}
        transport = _route_device_flow(state)
        client = MSGraphClient(http=httpx.AsyncClient(transport=transport))

        tokens = await client.poll_device_flow(
            _DEVICE_RESP["device_code"],
            interval=0.001,
            timeout=1.0,
        )

        assert tokens["access_token"] == "at-new"
        assert tokens["refresh_token"] == "rt-new"

    async def test_poll_device_flow_用户拒绝授权_抛出AuthError含错误码(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(
                _TOKEN_URL,
                {"error": "authorization_declined"},
            )

        client = MSGraphClient(
            http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

        with pytest.raises(MSGraphAuthError, match="authorization_declined"):
            await client.poll_device_flow("dc-1", interval=0.001, timeout=1.0)

    async def test_poll_device_flow_超时未授权_抛出AuthError(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(_TOKEN_URL, {"error": "authorization_pending"})

        client = MSGraphClient(
            http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

        with pytest.raises(MSGraphAuthError, match="超时"):
            await client.poll_device_flow("dc-1", interval=0.001, timeout=0.01)


class TestRefreshAndRequest:
    """refresh_tokens / request 测试."""

    async def test_refresh_tokens_刷新成功_更新内部token并触发回调(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            assert b"grant_type=refresh_token" in request.content
            return _json_response(_TOKEN_URL, _TOKEN_RESP)

        refreshed: list[dict] = []

        async def on_updated(tokens: dict) -> None:
            refreshed.append(tokens)

        client = MSGraphClient(
            tokens={"access_token": "at-old", "refresh_token": "rt-old"},
            http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            on_tokens_updated=on_updated,
        )

        new_tokens = await client.refresh_tokens()

        assert new_tokens["refresh_token"] == "rt-new"
        assert refreshed == [_TOKEN_RESP]

    async def test_request_401自动刷新重试_返回重试结果(self):
        state = {"graph_401_once": True}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "login.microsoftonline.com":
                return _json_response(_TOKEN_URL, _TOKEN_RESP)
            if state["graph_401_once"]:
                state["graph_401_once"] = False
                return _json_response(
                    f"{_GRAPH_BASE}/me/todo/lists",
                    {"error": "InvalidAuthenticationToken"},
                    401,
                )
            return _json_response(
                f"{_GRAPH_BASE}/me/todo/lists",
                {"value": [{"id": "lid-1", "displayName": "Tasks"}]},
            )

        refreshed: list[dict] = []

        async def on_updated(tokens: dict) -> None:
            refreshed.append(tokens)

        client = MSGraphClient(
            tokens={"access_token": "at-old", "refresh_token": "rt-old"},
            http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            on_tokens_updated=on_updated,
        )

        status, data = await client.request("GET", "/me/todo/lists")

        assert status == 200
        assert data["value"][0]["id"] == "lid-1"
        assert refreshed == [_TOKEN_RESP]

    async def test_request_刷新后仍401_返回401不无限重试(self):
        state = {"refresh_count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "login.microsoftonline.com":
                state["refresh_count"] += 1
                return _json_response(_TOKEN_URL, _TOKEN_RESP)
            return _json_response(
                f"{_GRAPH_BASE}/me/events",
                {"error": "InvalidAuthenticationToken"},
                401,
            )

        client = MSGraphClient(
            tokens={"access_token": "at-old", "refresh_token": "rt-old"},
            http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        status, _ = await client.request("GET", "/me/events")

        assert status == 401
        assert state["refresh_count"] == 1

    async def test_request_带时区偏好_请求头携带Prefer(self):
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(request.headers)
            return _json_response(f"{_GRAPH_BASE}/me/events", {"value": []})

        client = MSGraphClient(
            tokens={"access_token": "at-ok", "refresh_token": "rt-ok"},
            http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        status, _ = await client.request(
            "GET",
            "/me/events",
            prefer_timezone="Asia/Shanghai",
        )

        assert status == 200
        assert captured["prefer"] == 'outlook.timezone="Asia/Shanghai"'
        assert captured["authorization"] == "Bearer at-ok"

    async def test_request_未携带token_直接抛出AuthError(self):
        client = MSGraphClient(
            http=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: _json_response(_GRAPH_BASE, {}, 500)
                )
            )
        )

        with pytest.raises(MSGraphAuthError, match="access_token"):
            await client.request("GET", "/me")
