"""定时消息工具共享基类.

提供 schedule_message / list_scheduled_messages / cancel_scheduled_message
三个子工具的公共逻辑: 渠道检查/时区解析/Service获取/邮件地址解析.
"""

from __future__ import annotations

import logging
from typing import Any, override

from src.tools.shared.base_internal_tool import BaseInternalTool

logger = logging.getLogger(__name__)


class ScheduledMessengerBase(BaseInternalTool):
    """定时消息工具共享基类.

    子工具共享渠道检查/时区/Service/邮件地址解析逻辑,
    各自实现 _apply_description 与 _arun.
    """

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        *,
        agent_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(user_id, thread_id, agent_id=agent_id, **kwargs)
        self._service: Any = None
        self._tool_config: dict[str, Any] = kwargs
        self._available_channels: list[str] | None = None
        self._timezone: str | None = None

    def _load_shared_config(self) -> dict[str, Any]:
        """加载工具配置, 回退到 scheduled_messenger 共享配置段.

        子工具自身config为空时, 从 tools_config 的 scheduled_messenger 条目
        读取 SMTP/限额等共享配置, 避免在三个子工具中重复维护.
        """
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

    def _get_timezone(self) -> str:
        """获取用户时区配置."""
        if self._timezone is not None:
            return self._timezone

        try:
            from ...auth.auth_manager import get_auth_manager

            tz = get_auth_manager().get_user_timezone(self.user_id)
            self._timezone = tz
            return tz
        except Exception as e:
            logger.debug("用户时区获取失败, 使用默认Asia/Shanghai: %s", e)
            self._timezone = "Asia/Shanghai"
            return "Asia/Shanghai"

    async def _check_channels(self) -> list[str]:
        """检查用户已配置的可用渠道, 返回可用渠道类型列表."""
        if self._available_channels is not None:
            return self._available_channels

        try:
            from ...storage.service.user_channel_config_service import (
                get_user_channel_config_service,
            )

            config_service = await get_user_channel_config_service(
                self.user_id,
                self.thread_id,
                self.agent_id,
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

    def _check_smtp_config(self) -> bool:
        """检查SMTP配置是否完整, 决定email渠道是否可用(读系统级 smtp 配置)."""
        from src.config.smtp_config import is_configured

        return is_configured()

    @override
    async def is_available(self) -> bool:
        """检查渠道可用性, 决定是否注册.

        wechat: 需要用户有channel_config且target非空.
        email: SMTP配置完整即视为可用, 收件地址可在首次使用时录入.
        """
        channels = await self._check_channels()
        has_wechat = "wechat" in channels
        has_email = self._check_smtp_config()

        if not has_wechat and not has_email:
            return False

        self._apply_description(has_wechat=has_wechat, has_email=has_email)
        return True

    def _apply_description(self, *, has_wechat: bool, has_email: bool) -> None:
        """根据可用渠道动态调整工具描述(子类覆盖)."""

    async def _get_service(self) -> Any:
        if self._service is not None:
            return self._service

        from ...storage.service.scheduled_message_service import (
            get_scheduled_message_service,
        )

        config = self._load_shared_config()
        service_kwargs = {}
        for key in (
            "max_pending_messages",
            "max_schedule_ahead_hours",
            "default_channel",
        ):
            if key in config:
                service_kwargs[key] = config[key]

        service = await get_scheduled_message_service(
            self.user_id,
            self.thread_id,
            self.agent_id,
            **service_kwargs,
        )
        self._service = service
        return service

    async def _resolve_email_address(self, email_address: str | None) -> str | None:
        """解析邮件渠道的收件地址.

        - 提供了email_address → 写入/更新channel_config, 清除service缓存
        - 未提供但数据库已有 → 使用已保存的地址
        - 未提供且数据库也没有 → 返回错误消息

        Returns:
            None表示成功, 非None字符串表示错误消息

        """
        try:
            from ...storage.service.user_channel_config_service import (
                get_user_channel_config_service,
            )

            config_service = await get_user_channel_config_service(
                self.user_id,
                self.thread_id,
                self.agent_id,
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

    def _resolve_default_channel(self) -> str:
        """根据可用渠道推断默认渠道."""
        channels: list[str] = self._available_channels or []
        if "wechat" in channels:
            return "wechat"
        return "email"


__all__ = ["ScheduledMessengerBase"]
