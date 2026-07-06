"""统一通知派发基础设施.

NotificationService 屏蔽渠道细节, 业务方提供 DeliverySpec + 内容即可发送.
渠道后端:
- wechat → OpenClawClient (src/core/openclaw_client.py)
- email  → EmailClient (src/core/email_client.py)

resolve_delivery() 收敛渠道配置解析 (原散落在 scheduled_message_service /
openclaw_message_splitter / 价格监控工具三处重复), 并从
openclaw.notification_defaults 统一读取系统级渠道名 (消除来源不一致).

设计原则 (对齐 openclaw_client.py):
- 模块级单例 + 工厂函数
- 失败不抛异常, 返回 bool, 调用方决定降级策略

分层: 本模块位于 core 叶子层, 仅依赖 core/config; 对 storage.service 的访问
经延迟 import (运行时解析, 避免 core→storage 静态依赖).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.core.email_client import get_email_client
from src.core.lifecycle import register_resource
from src.core.openclaw_client import get_openclaw_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliverySpec:
    """统一投递描述, 屏蔽渠道细节.

    wechat 渠道: openclaw_channel / account_id / target 必填.
    email 渠道: email_address 必填.
    """

    method: str  # "wechat" | "email"
    # wechat (OpenClaw)
    openclaw_channel: str = ""
    account_id: str = ""
    target: str = ""
    # email
    email_address: str = ""


class NotificationService:
    """统一通知派发单例, 按 DeliverySpec.method 分流到对应渠道后端."""

    async def send(
        self,
        delivery: DeliverySpec,
        text: str,
        *,
        subject: str = "",
        html: str | None = None,
    ) -> bool:
        """按投递描述发送通知.

        Args:
            delivery: 投递描述 (method + 收件信息)
            text: 消息正文 (wechat 直接发送; email 作为纯文本正文)
            subject: 主题 (仅 email 渠道使用)
            html: 可选 HTML 正文 (仅 email 渠道, 提供 text+html 双部分)

        Returns:
            True 成功, False 失败 (失败日志已记录)
        """
        if delivery.method == "wechat":
            return await get_openclaw_client().send_message(
                channel=delivery.openclaw_channel,
                account_id=delivery.account_id,
                target=delivery.target,
                text=text,
            )
        if delivery.method == "email":
            return await get_email_client().send_email(
                to=delivery.email_address,
                subject=subject or "通知",
                body=text,
                html=html,
            )
        logger.error("不支持的投递方式: %s", delivery.method)
        return False

    async def close(self) -> None:
        """无持久资源, 占位以满足 LifecycleRegistry close 契约."""


_service_instance: NotificationService | None = None


def get_notification_service() -> NotificationService:
    """获取或创建 NotificationService 单例.

    第一次调用时创建实例并自注册到 LifecycleRegistry, 后续调用复用.
    """
    global _service_instance
    if _service_instance is not None:
        return _service_instance
    _service_instance = NotificationService()
    register_resource("notification", close_notification_service)
    logger.info("🔧 NotificationService 已创建")
    return _service_instance


async def close_notification_service() -> None:
    """应用关闭时调用, 重置单例."""
    global _service_instance
    if _service_instance is not None:
        await _service_instance.close()
        _service_instance = None


async def resolve_delivery(
    user_id: str,
    thread_id: str,
    agent_id: str,
    channel: str,
) -> DeliverySpec | None:
    """从 user 渠道配置解析投递描述.

    收敛渠道配置解析重复 (原散落在 scheduled_message / openclaw_message_splitter /
    价格监控工具三处), 并统一从 openclaw.notification_defaults 读取系统级渠道名.

    Args:
        user_id / thread_id / agent_id: 属主 (渠道配置按此物理隔离)
        channel: 渠道类型 ("wechat" | "email")

    Returns:
        投递描述; 用户未配置该渠道或关键字段缺失时返回 None.
    """
    from src.storage.service.user_channel_config_service import (
        get_user_channel_config_service,
    )

    try:
        config_service = await get_user_channel_config_service(
            user_id, thread_id, agent_id
        )
        cfg = await config_service.get_config_for_channel(channel)
    except Exception as e:
        logger.warning("解析渠道配置失败 (%s/%s): %s", user_id, channel, e)
        return None

    if not cfg:
        return None

    if channel == "wechat":
        target = cfg.get("target", "")
        account_id = cfg.get("openclaw_account", "")
        channel_key = cfg.get("openclaw_channel_key", "weixin")
        openclaw_channel = _resolve_openclaw_channel(channel_key)
        if not target or not account_id or not openclaw_channel:
            logger.warning(
                "wechat 渠道配置不完整: target_ok=%s, account_ok=%s, channel_ok=%s",
                bool(target),
                bool(account_id),
                bool(openclaw_channel),
            )
            return None
        return DeliverySpec(
            method="wechat",
            openclaw_channel=openclaw_channel,
            account_id=account_id,
            target=target,
        )

    if channel == "email":
        email_address = cfg.get("email_address", "")
        if not email_address:
            return None
        return DeliverySpec(method="email", email_address=email_address)

    logger.warning("未知渠道类型: %s", channel)
    return None


def _resolve_openclaw_channel(channel_key: str) -> str:
    """从 openclaw.notification_defaults 读取系统级渠道名."""
    try:
        from src.config.openclaw_config import get_config

        defaults = get_config().notification_defaults
    except Exception as e:
        logger.warning("读取 openclaw.notification_defaults 失败: %s", e)
        return ""
    entry = defaults.get(channel_key)
    return entry.channel if entry else ""


__all__ = [
    "DeliverySpec",
    "NotificationService",
    "close_notification_service",
    "get_notification_service",
    "resolve_delivery",
]
