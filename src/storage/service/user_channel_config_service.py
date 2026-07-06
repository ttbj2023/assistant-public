"""用户渠道配置业务服务.

管理用户的消息渠道配置, 包括查询默认渠道,按类型查询等.
渠道类型:
- wechat: 通过OpenClaw发送, config包含openclaw_channel/openclaw_account/target
- email: 通过SMTP发送, config包含email_address
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, override

from src.storage.dao.async_user_channel_config_dao import AsyncUserChannelConfigDAO
from src.storage.models.user_channel_config import UserChannelConfig

from .health_check_mixin import ServiceHealthCheckMixin

logger = logging.getLogger(__name__)


class UserChannelConfigService(ServiceHealthCheckMixin):
    """用户渠道配置业务服务."""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        user_id: str,
    ) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.user_id = user_id
        self.logger = logging.getLogger(f"{__name__}.UserChannelConfigService")

        self.dao = AsyncUserChannelConfigDAO(session_factory)

    async def get_config_for_channel(
        self,
        channel_type: str,
    ) -> dict[str, Any] | None:
        """获取指定渠道类型的配置字典."""
        config = await self.dao.get_config_by_type(self.user_id, channel_type)
        if config:
            return config.get_config_dict()
        return None

    async def upsert_channel_config(
        self,
        channel_type: str,
        config: dict[str, Any],
        is_default: bool = False,
    ) -> UserChannelConfig:
        """创建或更新渠道配置."""
        existing = await self.dao.get_config_by_type(self.user_id, channel_type)
        if existing:
            await self.dao.update_config(
                existing.id,
                config=config,
                is_default=is_default,
            )
            return await self.dao.get_by_id(existing.id)  # type: ignore[return-value]

        return await self.dao.create_config(
            user_id=self.user_id,
            channel_type=channel_type,
            config=config,
            is_default=is_default,
        )

    async def list_configs(self) -> list[UserChannelConfig]:
        return await self.dao.get_all_configs(self.user_id)

    @override
    async def _check_service_health(self) -> dict[str, Any]:
        try:
            db_ok = await self.dao.health_check()
            return {
                "status": "healthy" if db_ok else "unhealthy",
                "database_connected": db_ok,
                "error": None,
            }
        except Exception as e:
            self.logger.debug("用户渠道配置健康检查失败: %s", e)
            return {
                "status": "unhealthy",
                "database_connected": False,
                "error": str(e),
            }


async def get_user_channel_config_service(
    user_id: str,
    thread_id: str,
    agent_id: str,
) -> UserChannelConfigService:
    """创建UserChannelConfigService实例 (底层Engine全局复用).

    渠道配置按 (user, thread, agent) 物理隔离.
    """
    from src.storage.dao.async_database_manager import (
        create_async_channel_config_db_manager,
    )

    db_manager = await create_async_channel_config_db_manager(
        user_id,
        thread_id,
        agent_id,
    )

    return UserChannelConfigService(
        session_factory=db_manager.session_factory,
        user_id=user_id,
    )


__all__ = [
    "UserChannelConfigService",
    "get_user_channel_config_service",
]
