"""scheduled_message_cascade 单元测试.

影子级联: 日程写操作触发跨库影子消息同步(取消/顺延/统计).
scheduled_message 库按 (user, thread, agent) 物理分库, 级联枚举该用户
全部分库逐库处理. Mock: discover_scheduled_message_dbs + db manager 构造.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.storage.models.scheduled_message import MessageStatus
from src.storage.service.scheduled_message_cascade import (
    cancel_shadows_for_event,
    count_pending_shadows,
    reschedule_shadows_for_event,
)


def _mock_manager_factory():
    """构造 session_factory 各不相同的 db manager mock."""
    manager = MagicMock()
    manager.session_factory = MagicMock()
    return manager


def _mock_dao(pending_shadows: list):
    dao = MagicMock()
    dao.get_pending_by_related_event = AsyncMock(return_value=list(pending_shadows))
    dao.update_status = AsyncMock(return_value=True)
    dao.update_send_time = AsyncMock(return_value=True)
    return dao


def _shadow(message_id: str, send_time: datetime, related_event_id: int = 42):
    msg = MagicMock()
    msg.message_id = message_id
    msg.send_time = send_time
    msg.related_event_id = related_event_id
    return msg


class TestCancelShadows:
    @pytest.mark.asyncio
    async def test_cancels_pending_shadows_across_threads(self):
        """同用户多分库的影子都应被取消, 其他用户分库不触碰."""
        shadow_a = _shadow("msg-a", datetime(2026, 6, 2, 1, 0))
        shadow_b = _shadow("msg-b", datetime(2026, 6, 3, 1, 0))
        dao_a, dao_b = _mock_dao([shadow_a]), _mock_dao([shadow_b])
        daos = [dao_a, dao_b]
        manager = _mock_manager_factory()

        with (
            patch(
                "src.storage.service.scheduled_message_service."
                "discover_scheduled_message_dbs",
                return_value=[
                    ("u1", "t1", "a1"),
                    ("u1", "t2", "a1"),
                    ("u2", "t1", "a1"),
                ],
            ),
            patch(
                "src.storage.dao.async_database_manager."
                "create_async_scheduled_message_db_manager",
                AsyncMock(return_value=manager),
            ),
            patch(
                "src.storage.service.scheduled_message_cascade."
                "AsyncScheduledMessageDAO",
                side_effect=lambda sf: daos.pop(0),
            ),
        ):
            cancelled = await cancel_shadows_for_event("u1", 42)

        assert sorted(cancelled) == ["msg-a", "msg-b"]
        dao_a.update_status.assert_awaited_once_with("msg-a", MessageStatus.CANCELLED)
        dao_b.update_status.assert_awaited_once_with("msg-b", MessageStatus.CANCELLED)

    @pytest.mark.asyncio
    async def test_no_shadows_returns_empty(self):
        dao = _mock_dao([])
        manager = _mock_manager_factory()
        with (
            patch(
                "src.storage.service.scheduled_message_service."
                "discover_scheduled_message_dbs",
                return_value=[("u1", "t1", "a1")],
            ),
            patch(
                "src.storage.dao.async_database_manager."
                "create_async_scheduled_message_db_manager",
                AsyncMock(return_value=manager),
            ),
            patch(
                "src.storage.service.scheduled_message_cascade."
                "AsyncScheduledMessageDAO",
                return_value=dao,
            ),
        ):
            cancelled = await cancel_shadows_for_event("u1", 42)
        assert cancelled == []
        dao.update_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_open_failure_tolerated(self):
        """单库打开失败跳过, 不阻断其余分库级联."""
        shadow = _shadow("msg-c", datetime(2026, 6, 2, 1, 0))
        dao = _mock_dao([shadow])
        manager = _mock_manager_factory()
        manager_for = {"t1": Exception("boom"), "t2": _mock_manager_factory()}

        async def fake_create(uid, tid, *, agent_id):
            if isinstance(manager_for[tid], Exception):
                raise manager_for[tid]
            return manager_for[tid]

        with (
            patch(
                "src.storage.service.scheduled_message_service."
                "discover_scheduled_message_dbs",
                return_value=[("u1", "t1", "a1"), ("u1", "t2", "a1")],
            ),
            patch(
                "src.storage.dao.async_database_manager."
                "create_async_scheduled_message_db_manager",
                side_effect=fake_create,
            ),
            patch(
                "src.storage.service.scheduled_message_cascade."
                "AsyncScheduledMessageDAO",
                return_value=dao,
            ),
        ):
            cancelled = await cancel_shadows_for_event("u1", 42)

        assert cancelled == ["msg-c"]


class TestRescheduleShadows:
    @pytest.mark.asyncio
    async def test_shifts_send_time_by_delta(self):
        """影子发送时间按日程时间偏移顺延 (naive UTC 约定)."""
        shadow = _shadow("msg-a", datetime(2026, 6, 2, 6, 0))
        dao = _mock_dao([shadow])
        manager = _mock_manager_factory()

        with (
            patch(
                "src.storage.service.scheduled_message_service."
                "discover_scheduled_message_dbs",
                return_value=[("u1", "t1", "a1")],
            ),
            patch(
                "src.storage.dao.async_database_manager."
                "create_async_scheduled_message_db_manager",
                AsyncMock(return_value=manager),
            ),
            patch(
                "src.storage.service.scheduled_message_cascade."
                "AsyncScheduledMessageDAO",
                return_value=dao,
            ),
        ):
            rescheduled = await reschedule_shadows_for_event(
                "u1", 42, timedelta(hours=1)
            )

        assert rescheduled == ["msg-a"]
        new_time = dao.update_send_time.call_args.args[1]
        assert new_time == datetime(2026, 6, 2, 7, 0)
        assert new_time.tzinfo is None


class TestCountPendingShadows:
    @pytest.mark.asyncio
    async def test_sums_across_dbs(self):
        dao_a = _mock_dao([_shadow("msg-a", datetime(2026, 6, 2, 1, 0))])
        dao_b = _mock_dao([
            _shadow("msg-b", datetime(2026, 6, 3, 1, 0)),
            _shadow("msg-c", datetime(2026, 6, 4, 1, 0)),
        ])
        daos = [dao_a, dao_b]
        manager = _mock_manager_factory()

        with (
            patch(
                "src.storage.service.scheduled_message_service."
                "discover_scheduled_message_dbs",
                return_value=[("u1", "t1", "a1"), ("u1", "t2", "a1")],
            ),
            patch(
                "src.storage.dao.async_database_manager."
                "create_async_scheduled_message_db_manager",
                AsyncMock(return_value=manager),
            ),
            patch(
                "src.storage.service.scheduled_message_cascade."
                "AsyncScheduledMessageDAO",
                side_effect=lambda sf: daos.pop(0),
            ),
        ):
            count = await count_pending_shadows("u1", 42)

        assert count == 3
