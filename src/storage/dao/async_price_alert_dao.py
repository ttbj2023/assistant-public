"""异步价格监控规则数据访问对象.

基于 AsyncDatabaseOperations 泛型基类, Agent 物理隔离 db 内操作.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from ..models.price_alert import AlertStatus, PriceAlertRule
from .database_operations import AsyncDatabaseOperations

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class AsyncPriceAlertDAO:
    """异步价格监控规则数据访问对象."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.db_ops = AsyncDatabaseOperations(session_factory, PriceAlertRule)
        self.session_factory = session_factory

    async def create(self, **fields: Any) -> PriceAlertRule:
        """创建规则."""
        try:
            async with self.session_factory() as session:
                rule = PriceAlertRule(**fields)
                session.add(rule)
                await session.commit()
                await session.refresh(rule)
                return rule
        except Exception as e:
            logger.error("创建价格监控规则失败: %s", e)
            raise

    async def get(self, rule_id: str) -> PriceAlertRule | None:
        """按 rule_id 查询单条规则."""
        try:
            async with self.session_factory() as session:
                stmt = select(PriceAlertRule).where(PriceAlertRule.rule_id == rule_id)
                result = await session.execute(stmt)
                return result.scalar_one_or_none()
        except Exception as e:
            logger.error("查询规则 %s 失败: %s", rule_id, e)
            raise

    async def list_active_by_owner(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
    ) -> list[PriceAlertRule]:
        """按属主列出活跃规则 (按创建时间倒序)."""
        try:
            async with self.session_factory() as session:
                stmt = (
                    select(PriceAlertRule)
                    .where(
                        PriceAlertRule.user_id == user_id,
                        PriceAlertRule.thread_id == thread_id,
                        PriceAlertRule.agent_id == agent_id,
                        PriceAlertRule.status == AlertStatus.ACTIVE,
                    )
                    .order_by(PriceAlertRule.created_at.desc())
                )
                result = await session.execute(stmt)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("按属主查询活跃规则失败: %s", e)
            raise

    async def list_active_all(self) -> list[PriceAlertRule]:
        """列出当前 db 内所有活跃规则 (轮询引擎遍历各 db 调用)."""
        try:
            async with self.session_factory() as session:
                stmt = select(PriceAlertRule).where(
                    PriceAlertRule.status == AlertStatus.ACTIVE
                )
                result = await session.execute(stmt)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("查询全部活跃规则失败: %s", e)
            raise

    async def disable(
        self,
        rule_id: str,
        owner: tuple[str, str, str],
        *,
        triggered: bool = False,
    ) -> bool:
        """置规则为 disabled (软删除, 校验属主).

        triggered=True 时同时记录 triggered_at (引擎一次性触发结束).
        返回是否命中并更新.
        """
        user_id, thread_id, agent_id = owner
        try:
            async with self.session_factory() as session:
                values: dict[str, Any] = {"status": AlertStatus.DISABLED}
                if triggered:
                    values["triggered_at"] = datetime.utcnow()
                stmt = (
                    update(PriceAlertRule)
                    .where(
                        PriceAlertRule.rule_id == rule_id,
                        PriceAlertRule.user_id == user_id,
                        PriceAlertRule.thread_id == thread_id,
                        PriceAlertRule.agent_id == agent_id,
                        PriceAlertRule.status == AlertStatus.ACTIVE,
                    )
                    .values(**values)
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount > 0
        except Exception as e:
            logger.error("禁用规则 %s 失败: %s", rule_id, e)
            raise

    async def health_check(self) -> bool:
        return await self.db_ops.health_check()


__all__ = ["AsyncPriceAlertDAO"]
