"""异步定时消息数据访问对象."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from ..models.scheduled_message import MessageStatus, ScheduledMessage
from .database_operations import AsyncDatabaseOperations

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class AsyncScheduledMessageDAO:
    """异步定时消息数据访问对象."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.db_ops = AsyncDatabaseOperations(session_factory, ScheduledMessage)
        self.session_factory = session_factory

    async def create_message(
        self,
        message: str,
        send_time: datetime,
        user_id: str,
        thread_id: str,
        agent_id: str,
        description: str | None = None,
        channel: str = "wechat",
        subject: str | None = None,
        html_body: str | None = None,
    ) -> ScheduledMessage:
        return await self.db_ops.create_with_validation(
            required_fields=[
                "message",
                "send_time",
                "user_id",
                "thread_id",
                "agent_id",
            ],
            default_fields={
                "status": MessageStatus.PENDING,
                "description": description,
                "channel": channel,
                "subject": subject,
                "html_body": html_body,
            },
            message=message,
            send_time=send_time,
            user_id=user_id,
            thread_id=thread_id,
            agent_id=agent_id,
            description=description,
            channel=channel,
            subject=subject,
            html_body=html_body,
        )

    async def get_by_message_id(self, message_id: str) -> ScheduledMessage | None:
        try:
            async with self.session_factory() as session:
                stmt = select(ScheduledMessage).where(
                    ScheduledMessage.message_id == message_id,
                )
                result = await session.execute(stmt)
                return result.scalar_one_or_none()
        except Exception as e:
            logger.error("根据message_id查询失败: %s", e)
            raise

    async def get_pending_messages(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
    ) -> list[ScheduledMessage]:
        try:
            async with self.session_factory() as session:
                stmt = (
                    select(ScheduledMessage)
                    .where(
                        ScheduledMessage.user_id == user_id,
                        ScheduledMessage.thread_id == thread_id,
                        ScheduledMessage.agent_id == agent_id,
                        ScheduledMessage.status == MessageStatus.PENDING,
                    )
                    .order_by(ScheduledMessage.send_time)
                )
                result = await session.execute(stmt)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("查询待发送消息失败: %s", e)
            raise

    async def get_missed_messages(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
    ) -> list[ScheduledMessage]:
        try:
            async with self.session_factory() as session:
                stmt = (
                    select(ScheduledMessage)
                    .where(
                        ScheduledMessage.user_id == user_id,
                        ScheduledMessage.thread_id == thread_id,
                        ScheduledMessage.agent_id == agent_id,
                        ScheduledMessage.status == MessageStatus.MISSED,
                    )
                    .order_by(ScheduledMessage.send_time)
                )
                result = await session.execute(stmt)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("查询missed消息失败: %s", e)
            raise

    async def get_all_pending_across_users(self) -> list[ScheduledMessage]:
        try:
            async with self.session_factory() as session:
                stmt = (
                    select(ScheduledMessage)
                    .where(ScheduledMessage.status == MessageStatus.PENDING)
                    .order_by(ScheduledMessage.send_time)
                )
                result = await session.execute(stmt)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("查询全局pending消息失败: %s", e)
            raise

    async def update_status(
        self,
        message_id: str,
        status: MessageStatus,
        sent_at: datetime | None = None,
    ) -> bool:
        try:
            async with self.session_factory() as session:
                values: dict[str, Any] = {"status": status}
                if sent_at is not None:
                    values["sent_at"] = sent_at
                stmt = (
                    update(ScheduledMessage)
                    .where(ScheduledMessage.message_id == message_id)
                    .values(**values)
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount > 0
        except Exception as e:
            logger.error("更新消息状态失败: %s", e)
            raise

    async def mark_expired_as_missed(self, now: datetime) -> int:
        try:
            async with self.session_factory() as session:
                stmt = (
                    update(ScheduledMessage)
                    .where(
                        ScheduledMessage.status == MessageStatus.PENDING,
                        ScheduledMessage.send_time < now,
                    )
                    .values(status=MessageStatus.MISSED)
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount
        except Exception as e:
            logger.error("标记过期消息失败: %s", e)
            raise

    async def count_pending(self, user_id: str, thread_id: str, agent_id: str) -> int:
        try:
            async with self.session_factory() as session:
                from sqlalchemy import func

                stmt = (
                    select(func.count())
                    .select_from(ScheduledMessage)
                    .where(
                        ScheduledMessage.user_id == user_id,
                        ScheduledMessage.thread_id == thread_id,
                        ScheduledMessage.agent_id == agent_id,
                        ScheduledMessage.status == MessageStatus.PENDING,
                    )
                )
                result = await session.execute(stmt)
                return result.scalar() or 0
        except Exception as e:
            logger.error("统计pending消息数量失败: %s", e)
            raise

    async def health_check(self) -> bool:
        return await self.db_ops.health_check()
