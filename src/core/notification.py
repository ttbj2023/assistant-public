"""统一通知派发基础设施.

NotificationService 屏蔽渠道细节, 业务方提供 DeliverySpec + 内容即可发送.
渠道后端:
- wechat → ChannelPushClient (src/core/channel_push_client.py, 经渠道网关推送)
- email  → EmailClient (src/core/email_client.py)

resolve_delivery() 收敛渠道配置解析 (原散落在 scheduled_message_service /
价格监控工具两处重复).

设计原则 (对齐 channel_push_client.py):
- 模块级单例 + 工厂函数
- 失败不抛异常, 返回 bool, 调用方决定降级策略

分层: 本模块位于 core 叶子层, 仅依赖 core/config; 对 storage.service 的访问
经延迟 import (运行时解析, 避免 core→storage 静态依赖).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.core.channel_push_client import SendOutcome, get_channel_push_client
from src.core.email_client import get_email_client
from src.core.lifecycle import register_resource

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliverySpec:
    """统一投递描述, 屏蔽渠道细节.

    wechat 渠道: account_id / target 必填.
    email 渠道: email_address 必填.
    """

    method: str  # "wechat" | "email"
    # wechat (渠道网关)
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
    ) -> SendOutcome:
        """按投递描述发送通知.

        Args:
            delivery: 投递描述 (method + 收件信息)
            text: 消息正文 (wechat 直接发送; email 作为纯文本正文)
            subject: 主题 (仅 email 渠道使用)
            html: 可选 HTML 正文 (仅 email 渠道, 提供 text+html 双部分)

        Returns:
            SendOutcome: ok=True 成功; ok=False 时 error 为失败原因
        """
        if delivery.method == "wechat":
            return await get_channel_push_client().send_message(
                account_id=delivery.account_id,
                to=delivery.target,
                text=text,
                method="wechat",
            )
        if delivery.method == "email":
            ok = await get_email_client().send_email(
                to=delivery.email_address,
                subject=subject or "通知",
                body=text,
                html=html,
            )
            if ok:
                return SendOutcome(ok=True)
            # email 后端失败详情暂留应用日志, 此处给出可落库的概要
            return SendOutcome(ok=False, error="email 发送失败 (详见应用日志)")
        logger.error("不支持的投递方式: %s", delivery.method)
        return SendOutcome(ok=False, error=f"不支持的投递方式: {delivery.method}")

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

    收敛渠道配置解析重复 (原散落在 scheduled_message / 价格监控工具).

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
        account_id = cfg.get("account_id", "")
        if not target or not account_id:
            logger.warning(
                "wechat 渠道配置不完整: target_ok=%s, account_ok=%s",
                bool(target),
                bool(account_id),
            )
            return None
        return DeliverySpec(
            method="wechat",
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


__all__ = [
    "DeliverySpec",
    "NotificationService",
    "SendOutcome",
    "close_notification_service",
    "get_notification_service",
    "resolve_delivery",
]
