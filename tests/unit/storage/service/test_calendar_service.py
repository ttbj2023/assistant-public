"""CalendarService 单元测试.

测试用户级日历服务的业务逻辑: CRUD / 归属校验 / 时间对合并校验 / 范围查询.
Mock 外部依赖: AsyncCalendarEventDAO.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.storage.models.calendar_event import CalendarEvent, CalendarEventStatus
from src.storage.service.calendar_service import CalendarService


@pytest.fixture
def mock_session_factory():
    return MagicMock()


@pytest.fixture
def service(mock_session_factory):
    return CalendarService(mock_session_factory, user_id="user1")


@pytest.fixture
def sample_event():
    start = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    return CalendarEvent(
        title="团队周会",
        start_time=start,
        end_time=start + timedelta(hours=1),
        user_id="user1",
    )


class TestCreateEvent:
    """测试创建日程."""

    @pytest.mark.asyncio
    async def test_create_event_delegates_to_dao(self, service, sample_event):
        service.dao.create_event = AsyncMock(return_value=sample_event)

        result = await service.create_event(
            title="团队周会",
            start_time=sample_event.start_time,
            end_time=sample_event.end_time,
            description="每周同步",
            location="会议室A",
            source_thread_id="thread1",
            source_agent_id="agent1",
        )

        assert result == sample_event
        kwargs = service.dao.create_event.call_args.kwargs
        assert kwargs["user_id"] == "user1"
        assert kwargs["title"] == "团队周会"
        assert kwargs["source_thread_id"] == "thread1"

    @pytest.mark.asyncio
    async def test_create_event_success_notifies_graph_sync(
        self, service, sample_event
    ):
        """创建成功后应投递 Graph 同步即时信号."""
        service.dao.create_event = AsyncMock(return_value=sample_event)

        with patch(
            "src.storage.service.calendar_service._notify_graph_sync",
        ) as mock_notify:
            await service.create_event(
                title="团队周会",
                start_time=sample_event.start_time,
                end_time=sample_event.end_time,
            )

        mock_notify.assert_called_once_with("user1")

    @pytest.mark.asyncio
    async def test_create_event_dao_failure_wraps_runtime_error(self, service):
        service.dao.create_event = AsyncMock(side_effect=Exception("db down"))

        with pytest.raises(RuntimeError, match="创建日程失败"):
            await service.create_event(
                title="测试",
                start_time=datetime(2026, 9, 20, tzinfo=UTC),
                end_time=datetime(2026, 9, 20, 1, tzinfo=UTC),
            )


class TestGetEvent:
    """测试查询与归属校验."""

    @pytest.mark.asyncio
    async def test_get_event_owned(self, service, sample_event):
        service.dao.get_event_by_id = AsyncMock(return_value=sample_event)
        result = await service.get_event(1)
        assert result == sample_event

    @pytest.mark.asyncio
    async def test_get_event_other_user_returns_none(self, service, sample_event):
        sample_event.user_id = "user2"
        service.dao.get_event_by_id = AsyncMock(return_value=sample_event)
        result = await service.get_event(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_event_missing_returns_none(self, service):
        service.dao.get_event_by_id = AsyncMock(return_value=None)
        result = await service.get_event(999)
        assert result is None


class TestUpdateEvent:
    """测试更新与时间对校验."""

    @pytest.mark.asyncio
    async def test_update_event_owned(self, service, sample_event):
        service.dao.get_event_by_id = AsyncMock(return_value=sample_event)
        service.dao.update_event = AsyncMock(return_value=sample_event)

        result = await service.update_event(1, {"title": "改名"})
        assert result.event == sample_event
        service.dao.update_event.assert_called_once_with(1, {"title": "改名"})

    @pytest.mark.asyncio
    async def test_update_event_other_user_returns_none(self, service, sample_event):
        sample_event.user_id = "user2"
        service.dao.get_event_by_id = AsyncMock(return_value=sample_event)
        with patch.object(service.dao, "update_event", new=AsyncMock()) as mock_update:
            result = await service.update_event(1, {"title": "改名"})
        assert result.event is None
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_time_change_reschedules_shadows(
        self, service, sample_event
    ):
        """改时间应按偏移顺延影子消息并写入级联报告."""
        new_start = sample_event.start_time + timedelta(hours=2)
        updated = MagicMock()
        updated.start_time = new_start
        service.dao.get_event_by_id = AsyncMock(return_value=sample_event)
        service.dao.update_event = AsyncMock(return_value=updated)

        with patch(
            "src.storage.service.scheduled_message_cascade."
            "reschedule_shadows_for_event",
            new=AsyncMock(return_value=["msg-1"]),
        ) as mock_resched:
            result = await service.update_event(
                1, {"start_time": new_start, "end_time": new_start + timedelta(hours=1)}
            )

        assert result.event == updated
        assert result.cascade.rescheduled_message_ids == ["msg-1"]
        delta = mock_resched.call_args.args[2]
        assert delta == timedelta(hours=2)

    @pytest.mark.asyncio
    async def test_update_title_only_reports_linked_count(
        self, service, sample_event
    ):
        """仅改非时间字段不级联, 报告关联影子数量."""
        service.dao.get_event_by_id = AsyncMock(return_value=sample_event)
        service.dao.update_event = AsyncMock(return_value=sample_event)

        with (
            patch(
                "src.storage.service.scheduled_message_cascade."
                "reschedule_shadows_for_event",
                new=AsyncMock(return_value=[]),
            ) as mock_resched,
            patch(
                "src.storage.service.scheduled_message_cascade."
                "count_pending_shadows",
                new=AsyncMock(return_value=2),
            ),
        ):
            result = await service.update_event(1, {"title": "改名"})

        mock_resched.assert_not_awaited()
        assert result.cascade.linked_pending_count == 2
        assert result.cascade.rescheduled_message_ids == []

    @pytest.mark.asyncio
    async def test_update_event_conflicting_times_raises(self, service, sample_event):
        """仅更新 end_time 早于现有 start_time 应拒绝."""
        service.dao.get_event_by_id = AsyncMock(return_value=sample_event)

        with pytest.raises(RuntimeError, match="结束时间不能早于开始时间"):
            await service.update_event(
                1,
                {"end_time": sample_event.start_time - timedelta(hours=1)},
            )


class TestDeleteAndCancelEvent:
    """测试删除与软取消."""

    @pytest.mark.asyncio
    async def test_delete_event_owned(self, service, sample_event):
        service.dao.get_event_by_id = AsyncMock(return_value=sample_event)
        service.dao.delete_event = AsyncMock(return_value=True)
        result = await service.delete_event(1)
        assert result.deleted is True

    @pytest.mark.asyncio
    async def test_delete_event_cancels_shadows(self, service, sample_event):
        """删除日程应级联取消影子消息并写入报告."""
        service.dao.get_event_by_id = AsyncMock(return_value=sample_event)
        service.dao.delete_event = AsyncMock(return_value=True)

        with patch(
            "src.storage.service.scheduled_message_cascade."
            "cancel_shadows_for_event",
            new=AsyncMock(return_value=["msg-1", "msg-2"]),
        ) as mock_cancel:
            result = await service.delete_event(1)

        assert result.deleted is True
        assert result.cascade.cancelled_message_ids == ["msg-1", "msg-2"]
        mock_cancel.assert_awaited_once_with(service.user_id, 1)

    @pytest.mark.asyncio
    async def test_delete_event_other_user_returns_false(self, service, sample_event):
        sample_event.user_id = "user2"
        service.dao.get_event_by_id = AsyncMock(return_value=sample_event)
        with patch.object(service.dao, "delete_event", new=AsyncMock()) as mock_delete:
            result = await service.delete_event(1)
        assert result.deleted is False
        mock_delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_event_sets_status(self, service, sample_event):
        service.dao.get_event_by_id = AsyncMock(return_value=sample_event)
        service.dao.update_event = AsyncMock(return_value=sample_event)

        await service.cancel_event(1)
        update_data = service.dao.update_event.call_args.args[1]
        assert update_data["status"] == CalendarEventStatus.CANCELLED


class TestListEventsInRange:
    """测试时间范围查询."""

    @pytest.mark.asyncio
    async def test_delegates_to_dao(self, service, sample_event):
        service.dao.list_by_time_range = AsyncMock(return_value=[sample_event])

        window_start = datetime(2026, 9, 20, tzinfo=UTC)
        window_end = datetime(2026, 9, 21, tzinfo=UTC)
        result = await service.list_events_in_range(window_start, window_end)

        assert result == [sample_event]
        service.dao.list_by_time_range.assert_called_once_with(
            "user1",
            window_start,
            window_end,
        )
