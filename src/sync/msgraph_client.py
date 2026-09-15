"""Microsoft Graph (MSA 个人账户) OAuth 授权 + API 客户端.

授权通道移植自 homelab scripts/graph_*.py: 主通道为自建 app registration
「JFT Assistant」的 device code flow (client_id 经 MS_GRAPH_CLIENT_ID 注入),
拿 delegated token, 只能操作本人数据 (/me 视角); 自建 app 不可用时回退
第一方公共客户端 (见 DEFAULT_PUBLIC_CLIENT_ID). 本通道与 homelab CLI 通道
各自独立授权同一 client, 不共享 token 文件 (refresh token 每次刷新轮换,
两处共享同一文件会互相打断).

生命周期约定: http 客户端由调用方注入并持有 (引擎共享连接池), 本类不负责
关闭; tokens 为用户级状态, 每用户一个实例, 轮换经 on_tokens_updated 回调落盘.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from src.config.runtime_env import get_ms_graph_client_id

logger = logging.getLogger(__name__)

# Microsoft Graph Command Line Tools (第一方公共客户端, 非机密; 仅作自建 app
# 不可用时的应急 fallback, 与 homelab 通道的 fallback 语义一致)
DEFAULT_PUBLIC_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"

_TENANT = "consumers"
_DEVICE_URL = f"https://login.microsoftonline.com/{_TENANT}/oauth2/v2.0/devicecode"
_TOKEN_URL = f"https://login.microsoftonline.com/{_TENANT}/oauth2/v2.0/token"
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

# scope 首次授权即定全: delegated refresh 不能升级 scope, 后加必须重走授权;
# 与 homelab 通道对齐四件套 (联系人暂无消费方, 仅授权面预留, 避免将来重授权)
MSGRAPH_SCOPES = (
    "offline_access "
    "https://graph.microsoft.com/Tasks.ReadWrite "
    "https://graph.microsoft.com/Calendars.ReadWrite "
    "https://graph.microsoft.com/Contacts.ReadWrite"
)

_DEFAULT_POLL_INTERVAL = 5.0
_DEFAULT_POLL_TIMEOUT = 900.0
_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


class MSGraphAuthError(RuntimeError):
    """授权/认证流程错误 (拒绝授权/超时/刷新失败)."""

    def __init__(self, message: str, *, code: str = "") -> None:
        """初始化.

        Args:
            message: 错误信息
            code: OAuth 错误码 (如 authorization_declined), 非 OAuth 错误为空

        """
        super().__init__(message)
        self.code = code


class MSGraphClient:
    """单用户的 Graph 客户端 (device flow 发起/轮询, token 刷新, API 请求)."""

    def __init__(
        self,
        tokens: dict[str, Any] | None = None,
        *,
        http: httpx.AsyncClient,
        on_tokens_updated: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        """初始化.

        Args:
            tokens: OAuth token 响应 (含 access_token / refresh_token)
            http: 共享 httpx 客户端 (调用方持有生命周期)
            on_tokens_updated: token 轮换回调 (refresh 后触发, 用于持久化)

        """
        self._tokens = tokens or {}
        self._http = http
        self._on_tokens_updated = on_tokens_updated

    async def start_device_flow(self) -> dict[str, Any]:
        """发起 device code flow.

        Returns:
            含 verification_uri / user_code / device_code / expires_in / interval

        Raises:
            MSGraphAuthError: 发起失败

        """
        resp = await self._http.post(
            _DEVICE_URL,
            data={"client_id": get_ms_graph_client_id(), "scope": MSGRAPH_SCOPES},
        )
        data = _safe_json(resp, _DEVICE_URL)
        if resp.status_code != 200 or "device_code" not in data:
            raise MSGraphAuthError(
                f"发起 device flow 失败 HTTP {resp.status_code}: "
                f"{data.get('error', data)}",
                code=str(data.get("error", "")),
            )
        return data

    async def poll_device_flow(
        self,
        device_code: str,
        *,
        interval: float | None = None,
        timeout: float = _DEFAULT_POLL_TIMEOUT,
    ) -> dict[str, Any]:
        """轮询 device flow 直到用户完成授权.

        Args:
            device_code: start_device_flow 返回的 device_code
            interval: 轮询间隔 (默认取 OAuth 规范 5s, slow_down 自动加长)
            timeout: 总超时秒数

        Returns:
            OAuth token 响应 (含 access_token / refresh_token)

        Raises:
            MSGraphAuthError: 拒绝授权 / code 过期 / 超时

        """
        poll_interval = interval if interval is not None else _DEFAULT_POLL_INTERVAL
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            resp = await self._http.post(
                _TOKEN_URL,
                data={
                    "client_id": get_ms_graph_client_id(),
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                },
            )
            data = _safe_json(resp, _TOKEN_URL)
            error = data.get("error")
            if not error:
                self._tokens = data
                logger.info("MSA device flow 授权完成 (scope=%s)", data.get("scope"))
                return data
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                poll_interval += 5.0
                continue
            raise MSGraphAuthError(
                f"device flow 授权失败: {error} - "
                f"{str(data.get('error_description', ''))[:200]}",
                code=str(error),
            )
        raise MSGraphAuthError(
            f"device flow 授权超时 ({timeout:.0f}s) 未完成",
            code="timeout",
        )

    async def refresh_tokens(self) -> dict[str, Any]:
        """用 refresh_token 换新 token (轮换后触发回调持久化).

        Returns:
            新的 OAuth token 响应

        Raises:
            MSGraphAuthError: 缺 refresh_token 或刷新失败

        """
        refresh_token = self._tokens.get("refresh_token")
        if not refresh_token:
            raise MSGraphAuthError("缺少 refresh_token, 需重新走授权")
        resp = await self._http.post(
            _TOKEN_URL,
            data={
                "client_id": get_ms_graph_client_id(),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": MSGRAPH_SCOPES,
            },
        )
        data = _safe_json(resp, _TOKEN_URL)
        if resp.status_code != 200 or "access_token" not in data:
            raise MSGraphAuthError(
                f"token 刷新失败 HTTP {resp.status_code}: {data.get('error', data)}",
                code=str(data.get("error", "")),
            )
        self._tokens = data
        if self._on_tokens_updated is not None:
            await self._on_tokens_updated(data)
        return data

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        prefer_timezone: str | None = None,
    ) -> tuple[int, Any]:
        """调用 Graph API, 401 自动刷新重试一次.

        Args:
            method: HTTP 方法
            path: Graph 路径 (如 /me/todo/lists)
            json_body: JSON 请求体
            prefer_timezone: 时区偏好 (Prefer: outlook.timezone)

        Returns:
            (状态码, 解析后的 JSON; 空响应体为 None); 4xx/5xx 不抛异常

        Raises:
            MSGraphAuthError: 未携带 token
            httpx.HTTPError: 网络层失败

        """
        access_token = self._tokens.get("access_token")
        if not access_token:
            raise MSGraphAuthError("缺少 access_token, 需先授权或刷新")

        for attempt in range(2):
            headers = {"Authorization": f"Bearer {access_token}"}
            if prefer_timezone:
                headers["Prefer"] = f'outlook.timezone="{prefer_timezone}"'
            resp = await self._http.request(
                method,
                f"{GRAPH_BASE_URL}{path}",
                json=json_body,
                headers=headers,
            )
            if resp.status_code == 401 and attempt == 0:
                logger.info("Graph 401, 尝试刷新 token 后重试: %s %s", method, path)
                await self.refresh_tokens()
                access_token = str(self._tokens["access_token"])
                continue
            return resp.status_code, _safe_json(resp, f"{GRAPH_BASE_URL}{path}")
        raise MSGraphAuthError("unreachable")  # pragma: no cover


def _safe_json(resp: httpx.Response, url: str) -> Any:
    """解析响应 JSON, 空响应体返回 None, 解析失败给出可定位错误."""
    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError as e:
        raise MSGraphAuthError(f"{url} 响应非 JSON: {resp.text[:200]}") from e


__all__ = [
    "DEFAULT_PUBLIC_CLIENT_ID",
    "MSGRAPH_SCOPES",
    "MSGraphAuthError",
    "MSGraphClient",
]
