"""SMTP 邮件发送客户端 (公共通知基础设施).

封装 aiosmtplib 异步发送, 与 OpenClawClient 同为 NotificationService 的渠道后端.
复用 smtp_config.resolve_credentials() 读取系统级 SMTP 配置
(config.yaml smtp 段 + .env 回退).

设计原则 (对齐 openclaw_client.py):
- 模块级单例 + 工厂函数
- 失败不抛异常, 返回 bool, 调用方决定降级策略
- 结构化日志 (收件人前缀脱敏)
- SMTP 无持久连接, 每次发送即建即发即关, close() 为空操作

配置来源: smtp_config (config.yaml 顶层 smtp 段 + .env SMTP_USERNAME/SMTP_PASSWORD/
SMTP_FROM_ADDRESS 回退).
"""

from __future__ import annotations

import logging
from email.message import EmailMessage

from src.config.smtp_config import resolve_credentials
from src.core.lifecycle import register_resource

logger = logging.getLogger(__name__)


class EmailClient:
    """SMTP 邮件发送单例.

    通过 get_email_client() 获取实例, 全进程共享. SMTP 无连接态,
    配置在每次发送时实时解析(支持 .env 运行时热更新).
    """

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        html: str | None = None,
    ) -> bool:
        """发送一封邮件.

        Args:
            to: 收件人邮箱
            subject: 主题
            body: 纯文本正文
            html: 可选 HTML 正文 (提供时作为 multipart/alternative)

        Returns:
            True 成功, False 失败 (失败日志已记录)
        """
        if not to:
            logger.error("收件人为空, 邮件未发送")
            return False

        creds = resolve_credentials()
        if not creds.host or not creds.username or not creds.password:
            logger.error(
                "SMTP 配置不完整, 邮件未发送: host_ok=%s, username_ok=%s",
                bool(creds.host),
                bool(creds.username),
            )
            return False

        to_preview = to[:24]
        msg = EmailMessage()
        msg["From"] = creds.from_address
        msg["To"] = to
        msg["Subject"] = subject
        if html:
            msg.set_content(body)
            msg.add_alternative(html, subtype="html")
        else:
            msg.set_content(body)

        import aiosmtplib

        try:
            if creds.use_tls:
                await aiosmtplib.send(
                    msg,
                    hostname=creds.host,
                    port=creds.port,
                    username=creds.username,
                    password=creds.password,
                    use_tls=True,
                )
            else:
                await aiosmtplib.send(
                    msg,
                    hostname=creds.host,
                    port=creds.port,
                    username=creds.username,
                    password=creds.password,
                    start_tls=True,
                )
        except Exception as e:
            logger.error("邮件发送失败: to=%s, error=%s", to_preview, e)
            return False

        logger.info("✅ 邮件发送成功: to=%s", to_preview)
        return True

    async def close(self) -> None:
        """无持久资源, 占位以满足 LifecycleRegistry close 契约."""


_client_instance: EmailClient | None = None


def get_email_client() -> EmailClient:
    """获取或创建 EmailClient 单例.

    第一次调用时创建实例并自注册到 LifecycleRegistry, 后续调用复用.
    """
    global _client_instance
    if _client_instance is not None:
        return _client_instance
    _client_instance = EmailClient()
    register_resource("email", close_email_client)
    logger.info("🔧 EmailClient 已创建")
    return _client_instance


async def close_email_client() -> None:
    """应用关闭时调用, 重置单例."""
    global _client_instance
    if _client_instance is not None:
        await _client_instance.close()
        _client_instance = None


__all__ = ["EmailClient", "close_email_client", "get_email_client"]
