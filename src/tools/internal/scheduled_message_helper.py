"""定时消息共享逻辑 - 组合替代继承.

提供 ScheduledMessageHelper, 封装渠道检查/时区/Service/邮件地址解析.
具体工具通过组合 (lazy-init) 使用, 不再继承中间基类.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ScheduledMessageHelper:
    """定时消息共享逻辑 (组合).

    持有缓存 (_service/_channels/_timezone), 跨方法调用复用.
    """

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
        tool_config: dict[str, Any] | None = None,
    ) -> None:
        self._user_id = user_id
        self._thread_id = thread_id
        self._agent_id = agent_id
        self._tool_config: dict[str, Any] = tool_config or {}
        self._service: Any = None
        self._available_channels: list[str] | None = None
        self._timezone: str | None = None

    def load_shared_config(self) -> dict[str, Any]:
        """加载工具配置, 回退到 scheduled_messenger 共享配置段."""
        if self._tool_config:
            return self._tool_config
        try:
            from src.config.tools_config import get_config

            shared = get_config().get_internal_tool_config("scheduled_messenger")
            if shared and shared.config:
                return shared.config
        except Exception as e:
            logger.debug("读取 scheduled_messenger 共享配置失败: %s", e)
        return {}

    def get_timezone(self) -> str:
        """获取用户时区配置."""
        if self._timezone is not None:
            return self._timezone

        try:
            from src.auth.auth_manager import get_auth_manager

            tz = get_auth_manager().get_user_timezone(self._user_id)
            self._timezone = tz
            return tz
        except Exception as e:
            logger.debug("用户时区获取失败, 使用默认Asia/Shanghai: %s", e)
            self._timezone = "Asia/Shanghai"
            return "Asia/Shanghai"

    async def check_channels(self) -> list[str]:
        """检查用户已配置的可用渠道, 返回可用渠道类型列表."""
        if self._available_channels is not None:
            return self._available_channels

        try:
            from src.storage.service.user_channel_config_service import (
                get_user_channel_config_service,
            )

            config_service = await get_user_channel_config_service(
                self._user_id,
                self._thread_id,
                self._agent_id,
            )
            configs = await config_service.list_configs()

            available = []
            for cfg in configs:
                cfg_dict = cfg.get_config_dict()
                if cfg.channel_type == "wechat":
                    if cfg_dict.get("target"):
                        available.append("wechat")
                elif cfg.channel_type == "email" and cfg_dict.get("email_address"):
                    available.append("email")

            self._available_channels = available
            return available

        except Exception as e:
            logger.warning("检查渠道配置失败: %s", e)
            self._available_channels = []
            return []

    def check_smtp_config(self) -> bool:
        """检查 SMTP 配置是否完整."""
        from src.config.smtp_config import is_configured

        return is_configured()

    async def has_any_channel(self) -> bool:
        """任一发送渠道可用 (wechat 看用户渠道配置, email 看 SMTP)."""
        channels = await self.check_channels()
        return "wechat" in channels or self.check_smtp_config()

    async def get_service(self) -> Any:
        """获取 ScheduledMessageService (带缓存)."""
        if self._service is not None:
            return self._service

        from src.storage.service.scheduled_message_service import (
            get_scheduled_message_service,
        )

        config = self.load_shared_config()
        service_kwargs = {}
        for key in (
            "max_pending_messages",
            "max_schedule_ahead_hours",
            "default_channel",
        ):
            if key in config:
                service_kwargs[key] = config[key]

        service = await get_scheduled_message_service(
            self._user_id,
            self._thread_id,
            self._agent_id,
            **service_kwargs,
        )
        self._service = service
        return service

    async def resolve_email_address(self, email_address: str | None) -> str | None:
        """解析邮件渠道的收件地址.

        Returns:
            None 表示成功, 非 None 字符串表示错误消息

        """
        try:
            from src.storage.service.user_channel_config_service import (
                get_user_channel_config_service,
            )

            config_service = await get_user_channel_config_service(
                self._user_id,
                self._thread_id,
                self._agent_id,
            )
            existing = await config_service.get_config_for_channel("email")

            if email_address:
                await config_service.upsert_channel_config(
                    channel_type="email",
                    config={"email_address": email_address},
                )
                return None

            if existing and existing.get("email_address"):
                return None

            return (
                "错误: 使用邮件渠道需要提供收件邮箱地址(email_address参数). "
                "首次使用时系统会自动保存, 后续无需重复提供."
            )
        except Exception as e:
            logger.error("解析邮箱地址失败: %s", e)
            return f"错误: 解析邮箱地址失败: {e}"


__all__ = ["ScheduledMessageHelper"]
