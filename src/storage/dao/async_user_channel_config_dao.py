"""异步用户渠道配置数据访问对象."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from ..models.user_channel_config import UserChannelConfig
from .database_operations import AsyncDatabaseOperations

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class AsyncUserChannelConfigDAO:
    """异步用户渠道配置数据访问对象."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.db_ops = AsyncDatabaseOperations(session_factory, UserChannelConfig)
        self.session_factory = session_factory

    async def create_config(
        self,
        user_id: str,
        channel_type: str,
        config: dict[str, Any] | str,
        is_default: bool = False,
    ) -> UserChannelConfig:
        config_str = json.dumps(config) if isinstance(config, dict) else config
        return await self.db_ops.create_with_validation(
            required_fields=["user_id", "channel_type", "config"],
            default_fields={"is_default": is_default},
            user_id=user_id,
            channel_type=channel_type,
            config=config_str,
            is_default=is_default,
        )

    async def get_by_id(self, config_id: int) -> UserChannelConfig | None:
        try:
            async with self.session_factory() as session:
                stmt = select(UserChannelConfig).where(
                    UserChannelConfig.id == config_id,
                )
                result = await session.execute(stmt)
                return result.scalar_one_or_none()
        except Exception as e:
            logger.error("根据ID查询渠道配置失败: %s", e)
            raise

    async def get_default_config(self, user_id: str) -> UserChannelConfig | None:
        try:
            async with self.session_factory() as session:
                stmt = select(UserChannelConfig).where(
                    UserChannelConfig.user_id == user_id,
                    UserChannelConfig.is_default == True,  # noqa: E712
                )
                result = await session.execute(stmt)
                return result.scalar_one_or_none()
        except Exception as e:
            logger.error("查询默认渠道配置失败: %s", e)
            raise

    async def get_config_by_type(
        self,
        user_id: str,
        channel_type: str,
    ) -> UserChannelConfig | None:
        try:
            async with self.session_factory() as session:
                stmt = select(UserChannelConfig).where(
                    UserChannelConfig.user_id == user_id,
                    UserChannelConfig.channel_type == channel_type,
                )
                result = await session.execute(stmt)
                return result.scalar_one_or_none()
        except Exception as e:
            logger.error(
                "查询渠道配置失败: user=%s, type=%s, %s",
                user_id,
                channel_type,
                e,
            )
            raise

    async def get_all_configs(self, user_id: str) -> list[UserChannelConfig]:
        try:
            async with self.session_factory() as session:
                stmt = (
                    select(UserChannelConfig)
                    .where(
                        UserChannelConfig.user_id == user_id,
                    )
                    .order_by(UserChannelConfig.is_default.desc())
                )
                result = await session.execute(stmt)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("查询用户所有渠道配置失败: %s", e)
            raise

    async def update_config(
        self,
        config_id: int,
        config: dict[str, Any] | str | None = None,
        is_default: bool | None = None,
    ) -> bool:
        try:
            async with self.session_factory() as session:
                values: dict[str, Any] = {}
                if config is not None:
                    values["config"] = (
                        json.dumps(config) if isinstance(config, dict) else config
                    )
                if is_default is not None:
                    values["is_default"] = is_default
                if not values:
                    return False

                stmt = (
                    update(UserChannelConfig)
                    .where(UserChannelConfig.id == config_id)
                    .values(**values)
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount > 0
        except Exception as e:
            logger.error("更新渠道配置失败: %s", e)
            raise

    async def delete_config(self, config_id: int) -> bool:
        try:
            async with self.session_factory() as session:
                config = await session.get(UserChannelConfig, config_id)
                if not config:
                    return False
                await session.delete(config)
                await session.commit()
                return True
        except Exception as e:
            logger.error("删除渠道配置失败: %s", e)
            raise

    async def unset_all_defaults(self, user_id: str) -> int:
        try:
            async with self.session_factory() as session:
                stmt = (
                    update(UserChannelConfig)
                    .where(
                        UserChannelConfig.user_id == user_id,
                        UserChannelConfig.is_default == True,  # noqa: E712
                    )
                    .values(is_default=False)
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount
        except Exception as e:
            logger.error("取消默认渠道标记失败: %s", e)
            raise

    async def health_check(self) -> bool:
        return await self.db_ops.health_check()
