"""渠道推送网关 HTTP 客户端.

封装与渠道网关 (weixin-gateway 等) 的主动推送交互:
- send_message: 通过 POST /channel/send 推送文本消息
  (定时消息 / 价格提醒 / 通知)

设计原则:
- 模块级单例 + 工厂函数
- 复用单个 httpx.AsyncClient (连接池)
- 失败不抛异常, 返回 bool, 调用方决定降级策略
- 结构化日志 (含 target 前 16 字符脱敏, 避免泄露完整 user_id)

配置来源:
- URL: CHANNEL_GATEWAY_URL > config.yaml: channel_push.gateway.url > 默认值
- Token: credentials_registry -> channel_gateway_token
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from src.config.credentials_registry import get_credential
from src.config.runtime_env import get_channel_gateway_url
from src.core.lifecycle import register_resource

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://127.0.0.1:18789"
_DEFAULT_TIMEOUT_SECONDS = 30.0

# assistant 业务渠道名 → 网关渠道名 (当前仅微信网关)
_METHOD_TO_GATEWAY_CHANNEL = {"wechat": "weixin"}


@dataclass(frozen=True)
class SendOutcome:
    """发送结果: ok=False 时 error 携带失败原因 (供落库/上报)."""

    ok: bool
    error: str | None = None


def _resolve_config() -> tuple[str, str]:
    """读取配置."""
    url = get_channel_gateway_url()
    token = get_credential("channel_gateway_token")

    try:
        from src.config.channel_push_config import get_config

        gw = get_config().gateway
        url = url or gw.url or _DEFAULT_URL
    except Exception:
        logger.debug("读取 channel_push config 失败, 使用默认值/env", exc_info=True)
        url = url or _DEFAULT_URL

    return url, token


class ChannelPushClient:
    """渠道推送网关 HTTP 客户端单例.

    通过 get_channel_push_client() 获取实例, 全进程共享同一个 httpx.AsyncClient.
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
        account_id: str,
        to: str,
        text: str,
        *,
        method: str = "wechat",
    ) -> SendOutcome:
        """通过网关 /channel/send 推送文本消息.

        Args:
            account_id: 渠道 bot 账号 ID (如微信 bot 账号)
            to: 收消息用户地址 (如微信 OpenID 完整地址)
            text: 消息内容
            method: 业务渠道名 ("wechat"), 内部映射网关渠道名

        Returns:
            SendOutcome: ok=True 成功; ok=False 时 error 为失败原因
            (失败日志已记录, 含脱敏的 to 前缀)
        """
        gateway_channel = _METHOD_TO_GATEWAY_CHANNEL.get(method)
        if not gateway_channel:
            logger.error("未知的业务渠道: %s", method)
            return SendOutcome(ok=False, error=f"未知的业务渠道: {method}")

        payload = {
            "channel": gateway_channel,
            "account_id": account_id,
            "to": to,
            "text": text,
        }
        target_preview = to[:16] if to else "<empty>"
        try:
            resp = await self._client.post("/channel/send", json=payload)
        except httpx.HTTPError as e:
            logger.error("渠道推送网络异常: %s, to=%s", e, target_preview)
            return SendOutcome(ok=False, error=f"网关网络异常: {e}")

        if resp.status_code != 200:
            logger.error(
                "渠道推送失败: status=%d, body=%s, to=%s",
                resp.status_code,
                resp.text[:200],
                target_preview,
            )
            return SendOutcome(
                ok=False,
                error=f"网关HTTP {resp.status_code}: {resp.text[:200]}",
            )

        try:
            data = resp.json()
        except ValueError:
            logger.error(
                "渠道网关响应非JSON: to=%s, body=%s",
                target_preview,
                resp.text[:200],
            )
            return SendOutcome(
                ok=False,
                error=f"网关响应非JSON: {resp.text[:200]}",
            )

        if not data.get("ok"):
            error = str(data.get("error") or "(no error)")
            logger.error(
                "渠道推送失败: ok=false, error=%s, to=%s",
                error,
                target_preview,
            )
            return SendOutcome(ok=False, error=f"网关投递失败: {error}")

        logger.info("✅ 渠道推送成功: to=%s", target_preview)
        return SendOutcome(ok=True)

    async def close(self) -> None:
        """关闭底层 httpx client, 应用关闭时调用."""
        await self._client.aclose()


_client_instance: ChannelPushClient | None = None


def get_channel_push_client() -> ChannelPushClient:
    """获取或创建 ChannelPushClient 单例.

    第一次调用时根据 env/config.yaml 创建实例, 后续调用复用.
    """
    global _client_instance
    if _client_instance is not None:
        return _client_instance

    url, token = _resolve_config()
    if not token:
        logger.warning(
            "渠道网关 token 未配置, 推送调用将失败 (请设置 CHANNEL_GATEWAY_TOKEN)",
        )
    _client_instance = ChannelPushClient(url=url, token=token)
    register_resource("channel_push", close_channel_push_client)
    logger.info("🔧 ChannelPushClient 已创建: url=%s", url)
    return _client_instance


async def close_channel_push_client() -> None:
    """应用关闭时调用, 关闭单例 client."""
    global _client_instance
    if _client_instance is not None:
        await _client_instance.close()
        _client_instance = None


__all__ = [
    "ChannelPushClient",
    "SendOutcome",
    "close_channel_push_client",
    "get_channel_push_client",
]
