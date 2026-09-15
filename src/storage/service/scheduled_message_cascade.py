"""定时消息影子级联 — 日程写操作触发跨库影子消息同步.

影子 = 关联日程的定时消息 (related_event_id). scheduled_message 库按
(user, thread, agent) 物理分库, 级联需枚举该用户全部分库逐库处理:
- 取消以 DB 状态为准: _send_message 发送前复查状态, CANCELLED 即跳过
- 顺延以 DB send_time 为准: _send_message 到点复查未到点会重挂定时器
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import timedelta

from src.storage.dao.async_scheduled_message_dao import AsyncScheduledMessageDAO
from src.storage.models.scheduled_message import MessageStatus

logger = logging.getLogger(__name__)


async def _iter_shadow_daos(user_id: str) -> AsyncIterator[AsyncScheduledMessageDAO]:
    """枚举该用户的全部 scheduled_message 分库, 产出可用 DAO.

    单库打开失败记警告跳过, 不阻断其余分库.
    """
    from src.storage.dao.async_database_manager import (
        create_async_scheduled_message_db_manager,
    )
    from src.storage.service.scheduled_message_service import (
        discover_scheduled_message_dbs,
    )

    for uid, thread_id, agent_id in discover_scheduled_message_dbs():
        if uid != user_id:
            continue
        try:
            manager = await create_async_scheduled_message_db_manager(
                uid,
                thread_id,
                agent_id=agent_id,
            )
            yield AsyncScheduledMessageDAO(manager.session_factory)
        except Exception as e:
            logger.warning("级联打开分库失败 user=%s thread=%s: %s", uid, thread_id, e)


async def cancel_shadows_for_event(user_id: str, event_id: int) -> list[str]:
    """取消该日程的全部 pending 影子消息.

    Returns:
        已取消的 message_id 列表

    """
    cancelled: list[str] = []
    async for dao in _iter_shadow_daos(user_id):
        try:
            shadows = await dao.get_pending_by_related_event(user_id, event_id)
            for msg in shadows:
                if await dao.update_status(msg.message_id, MessageStatus.CANCELLED):
                    cancelled.append(msg.message_id)
        except Exception as e:
            logger.warning(
                "级联取消影子失败 user=%s event=%s: %s", user_id, event_id, e
            )
    if cancelled:
        logger.info("影子级联: 日程%s取消%d条影子消息", event_id, len(cancelled))
    return cancelled


async def reschedule_shadows_for_event(
    user_id: str,
    event_id: int,
    delta: timedelta,
) -> list[str]:
    """按日程时间偏移顺延该日程的全部 pending 影子消息.

    DB 时间为 naive UTC, 偏移运算保持该约定.

    Returns:
        已顺延的 message_id 列表

    """
    rescheduled: list[str] = []
    async for dao in _iter_shadow_daos(user_id):
        try:
            shadows = await dao.get_pending_by_related_event(user_id, event_id)
            for msg in shadows:
                new_send = msg.send_time + delta
                if await dao.update_send_time(msg.message_id, new_send):
                    rescheduled.append(msg.message_id)
        except Exception as e:
            logger.warning(
                "级联顺延影子失败 user=%s event=%s: %s", user_id, event_id, e
            )
    if rescheduled:
        logger.info("影子级联: 日程%s顺延%d条影子消息", event_id, len(rescheduled))
    return rescheduled


async def count_pending_shadows(user_id: str, event_id: int) -> int:
    """统计该日程的 pending 影子数 (非时间字段变更时的报告用)."""
    count = 0
    async for dao in _iter_shadow_daos(user_id):
        try:
            count += len(await dao.get_pending_by_related_event(user_id, event_id))
        except Exception as e:
            logger.warning(
                "级联统计影子失败 user=%s event=%s: %s", user_id, event_id, e
            )
    return count


__all__ = [
    "cancel_shadows_for_event",
    "count_pending_shadows",
    "reschedule_shadows_for_event",
]
