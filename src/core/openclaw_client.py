"""OpenClaw Gateway HTTP 客户端.

封装与 OpenClaw Gateway 的所有 HTTP 交互:
- send_message: 通过 POST /tools/invoke 调用 message send (主动发消息)
- get_bindings: 通过 POST /api/v1/admin/rpc config.get 拉 bindings (需 admin-http-rpc 插件)

设计原则:
- 模块级单例 + 工厂函数, 与 SharedHTTPClientManager 风格一致
- 复用单个 httpx.AsyncClient (连接池)
- 失败不抛异常, 返回 bool/Optional, 调用方决定降级策略
- 结构化日志 (含 target 前 16 字符脱敏, 避免泄露完整 user_id)

配置来源:
- URL: OPENCLAW_GATEWAY_URL > config.yaml: openclaw.gateway.url > 默认值
- Token: credentials_registry -> OPENCLAW_GATEWAY_TOKEN
"""

from __future__ import annotations

import logging

import httpx

from src.config.credentials_registry import get_credential
from src.config.runtime_env import get_openclaw_gateway_url
from src.core.lifecycle import register_resource

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://127.0.0.1:18789"
_DEFAULT_TIMEOUT_SECONDS = 30.0


def _resolve_config() -> tuple[str, str]:
    """读取配置."""
    url = get_openclaw_gateway_url()
    token = get_credential("openclaw_gateway_token")

    try:
        from src.config.openclaw_config import get_config

        gw = get_config().gateway
        url = url or gw.url or _DEFAULT_URL
    except Exception:
        logger.debug("读取 openclaw config 失败, 使用默认值/env", exc_info=True)
        url = url or _DEFAULT_URL

    return url, token


class OpenClawClient:
    """OpenClaw Gateway HTTP 客户端单例.

    通过 get_openclaw_client() 获取实例, 全进程共享同一个 httpx.AsyncClient.
    """

    def __init__(self, url: str, token: str) -> None:
        self._url = url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(
                connect=5.0,
                read=_DEFAULT_TIMEOUT_SECONDS,
                write=10.0,
                pool=5.0,
            ),
            limits=httpx.Limits(
                max_keepalive_connections=10,
                max_connections=20,
                keepalive_expiry=30.0,
            ),
        )

    async def send_message(
        self,
        channel: str,
        account_id: str,
        target: str,
        text: str,
    ) -> bool:
        """通过 /tools/invoke 调 message send.

        Args:
            channel: 渠道 ID, 如 "openclaw-weixin"
            account_id: 微信 bot 账号 ID
            target: 收消息用户 ID (微信 openid 等)
            text: 消息内容

        Returns:
            True 发送成功, False 失败 (失败日志已记录, 含脱敏的 target 前缀)
        """
        payload = {
            "tool": "message",
            "action": "send",
            "args": {
                "channel": channel,
                "accountId": account_id,
                "to": target,
                "content": text,
            },
        }
        target_preview = target[:16] if target else "<empty>"
        try:
            resp = await self._client.post("/tools/invoke", json=payload)
        except httpx.HTTPError as e:
            logger.error("OpenClaw发送网络异常: %s, target=%s", e, target_preview)
            return False

        if resp.status_code != 200:
            logger.error(
                "OpenClaw发送失败: status=%d, body=%s, target=%s",
                resp.status_code,
                resp.text[:200],
                target_preview,
            )
            return False

        try:
            data = resp.json()
        except ValueError:
            logger.error(
                "OpenClaw响应非JSON: target=%s, body=%s",
                target_preview,
                resp.text[:200],
            )
            return False

        if not data.get("ok"):
            logger.error(
                "OpenClaw发送失败: ok=false, error=%s, target=%s",
                data.get("error"),
                target_preview,
            )
            return False

        logger.info("✅ OpenClaw发送成功: target=%s", target_preview)
        return True

    async def close(self) -> None:
        """关闭底层 httpx client, 应用关闭时调用."""
        await self._client.aclose()


_client_instance: OpenClawClient | None = None


def get_openclaw_client() -> OpenClawClient:
    """获取或创建 OpenClawClient 单例.

    第一次调用时根据 env/config.yaml 创建实例, 后续调用复用.
    """
    global _client_instance
    if _client_instance is not None:
        return _client_instance

    url, token = _resolve_config()
    if not token:
        logger.warning(
            "OpenClaw Gateway token 未配置, 客户端调用将失败 (请设置 OPENCLAW_GATEWAY_TOKEN)",
        )
    _client_instance = OpenClawClient(url=url, token=token)
    register_resource("openclaw", close_openclaw_client)
    logger.info("🔧 OpenClawClient 已创建: url=%s", url)
    return _client_instance


async def close_openclaw_client() -> None:
    """应用关闭时调用, 关闭单例 client."""
    global _client_instance
    if _client_instance is not None:
        await _client_instance.close()
        _client_instance = None


__all__ = [
    "OpenClawClient",
    "close_openclaw_client",
    "get_openclaw_client",
]
