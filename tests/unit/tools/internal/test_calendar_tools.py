"""日历子工具单元测试.

覆盖 calendar_manager_group 四个子工具与 calendar_helpers 共享逻辑.
重点: 用户时区解析 (naive 输入按用户时区转 UTC), 全天事件跳过时区转换.
Mock: create_calendar_service.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.internal.calendar_helpers import (
    parse_event_datetime,
    parse_recurrence_freq,
)
from src.tools.internal.create_calendar_event_tool import CreateCalendarEventTool
from src.tools.internal.delete_calendar_event_tool import DeleteCalendarEventTool
from src.tools.internal.list_calendar_events_tool import ListCalendarEventsTool
from src.tools.internal.update_calendar_event_tool import UpdateCalendarEventTool
from src.tools.shared.tool_runtime import inject_identity


def _mock_acc(service):
    from src.tools.internal.calendar_helpers import CalendarServiceAccessor

    acc = CalendarServiceAccessor.__new__(CalendarServiceAccessor)
    acc._user_id = "u1"
    acc._thread_id = "t1"
    acc._agent_id = "a1"
    acc._service = service
    return acc


@pytest.fixture
def create_tool():
    tool = CreateCalendarEventTool()
    inject_identity(tool, "u1", "t1", "a1")
    return tool


@pytest.fixture
def list_tool():
    tool = ListCalendarEventsTool()
    inject_identity(tool, "u1", "t1", "a1")
    return tool


@pytest.fixture
def update_tool():
    tool = UpdateCalendarEventTool()
    inject_identity(tool, "u1", "t1", "a1")
    return tool


@pytest.fixture
def delete_tool():
    tool = DeleteCalendarEventTool()
    inject_identity(tool, "u1", "t1", "a1")
    return tool


def _make_event(event_id=1, title="测试日程", recurring=False):
    event = MagicMock()
    event.id = event_id
    event.event_uid = f"evt_{event_id:08d}"
    event.title = title
    event.description = None
    event.location = None
    start = datetime(2026, 9, 20, 2, 0, tzinfo=UTC)
    event.start_time = start
    event.end_time = start + timedelta(hours=1)
    event.all_day = False
    event.recurrence_rule = "FREQ=WEEKLY;INTERVAL=1" if recurring else None
    event.recurrence_until = None
    event.status = MagicMock(value="active")
    event.user_id = "u1"
    event.source_thread_id = None
    event.source_agent_id = None
    event.created_at = None
    event.updated_at = None
    return event


@pytest.fixture
def mock_service():
    from src.storage.service.calendar_service import (
        EventDeleteResult,
        EventUpdateResult,
        ShadowCascadeReport,
    )

    svc = AsyncMock()
    svc.create_event = AsyncMock(return_value=_make_event())
    svc.list_events_in_range = AsyncMock(return_value=[_make_event()])
    svc.get_event = AsyncMock(return_value=_make_event())
    svc.update_event = AsyncMock(
        return_value=EventUpdateResult(
            _make_event(), ShadowCascadeReport(linked_pending_count=0)
        )
    )
    svc.delete_event = AsyncMock(
        return_value=EventDeleteResult(True, ShadowCascadeReport())
    )
    return svc


class TestParseEventDatetime:
    """时间解析: naive 按用户时区转 UTC, all_day 保持日期."""

    def test_naive_treated_as_user_timezone(self) -> None:
        dt = parse_event_datetime(
            "2026-09-20T10:00:00",
            timezone="Asia/Shanghai",
            all_day=False,
        )
        assert dt == datetime(2026, 9, 20, 2, 0, tzinfo=UTC)

    def test_aware_passthrough(self) -> None:
        dt = parse_event_datetime(
            "2026-09-20T10:00:00+00:00",
            timezone="Asia/Shanghai",
            all_day=False,
        )
        assert dt == datetime(2026, 9, 20, 10, 0, tzinfo=UTC)

    def test_date_only_all_day_keeps_date(self) -> None:
        """全天事件: 日期字符串直接按 UTC 零点存 (ICS DATE 语义)."""
        dt = parse_event_datetime(
            "2026-09-20",
            timezone="Asia/Shanghai",
            all_day=True,
        )
        assert dt == datetime(2026, 9, 20, 0, 0, tzinfo=UTC)

    def test_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="时间格式"):
            parse_event_datetime("不是时间", timezone="Asia/Shanghai", all_day=False)


class TestParseRecurrenceFreq:
    """重复频率解析."""

    def test_valid_freqs(self) -> None:
        assert parse_recurrence_freq("weekly") == "FREQ=WEEKLY;INTERVAL=1"
        assert parse_recurrence_freq("每两周", interval=2) == "FREQ=WEEKLY;INTERVAL=2"
        assert parse_recurrence_freq("每天") == "FREQ=DAILY;INTERVAL=1"
        assert parse_recurrence_freq("每月") == "FREQ=MONTHLY;INTERVAL=1"
        assert parse_recurrence_freq("每年") == "FREQ=YEARLY;INTERVAL=1"

    def test_none_returns_none(self) -> None:
        assert parse_recurrence_freq(None) is None

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="不支持的重复频率"):
            parse_recurrence_freq("hourly")


# ========== CreateCalendarEventTool ==========


class TestCreateCalendarEvent:
    @pytest.mark.asyncio
    async def test_create_success(self, create_tool, mock_service):
        with patch.object(
            create_tool,
            "_get_accessor",
            return_value=_mock_acc(mock_service),
        ):
            result = await create_tool._arun(
                title="团队周会",
                start_time="2026-09-20T10:00:00",
                end_time="2026-09-20T11:00:00",
            )
        data = json.loads(result)
        assert data["success"] is True
        assert data["action"] == "created"
        mock_service.create_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_response_includes_reminder_guidance(
        self, create_tool, mock_service
    ):
        """创建响应应携带关联提醒引导: event_id 供定时消息 related_event_id 使用."""
        mock_service.create_event = AsyncMock(return_value=_make_event(event_id=77))
        with patch.object(
            create_tool,
            "_get_accessor",
            return_value=_mock_acc(mock_service),
        ):
            result = await create_tool._arun(
                title="团队周会",
                start_time="2026-09-20T10:00:00",
            )
        data = json.loads(result)
        assert "related_event_id" in data["reminder_hint"]
        assert "77" in data["reminder_hint"]

    @pytest.mark.asyncio
    async def test_create_recurring(self, create_tool, mock_service):
        with patch.object(
            create_tool,
            "_get_accessor",
            return_value=_mock_acc(mock_service),
        ):
            result = await create_tool._arun(
                title="每周例会",
                start_time="2026-09-20T10:00:00",
                recurrence_freq="weekly",
            )
        data = json.loads(result)
        assert data["success"] is True
        kwargs = mock_service.create_event.call_args.kwargs
        assert kwargs["recurrence_rule"] == "FREQ=WEEKLY;INTERVAL=1"

    @pytest.mark.asyncio
    async def test_create_naive_time_converted_via_user_tz(
        self,
        create_tool,
        mock_service,
    ):
        """naive 时间按用户时区 (默认 Asia/Shanghai) 转 UTC 后入参."""
        with (
            patch.object(
                create_tool,
                "_get_accessor",
                return_value=_mock_acc(mock_service),
            ),
            patch(
                "src.tools.internal.calendar_helpers.get_user_context_or_none",
                return_value=None,
            ),
        ):
            await create_tool._arun(
                title="时区测试",
                start_time="2026-09-20T10:00:00",
                end_time="2026-09-20T11:00:00",
            )
        kwargs = mock_service.create_event.call_args.kwargs
        assert kwargs["start_time"] == datetime(2026, 9, 20, 2, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_create_empty_title(self, create_tool):
        result = await create_tool._arun(
            title="  ",
            start_time="2026-09-20T10:00:00",
        )
        data = json.loads(result)
        assert data["success"] is False
        assert "标题不能为空" in data["message"]

    @pytest.mark.asyncio
    async def test_create_bad_time_format(self, create_tool):
        result = await create_tool._arun(
            title="测试",
            start_time="not-a-time",
        )
        data = json.loads(result)
        assert data["success"] is False
        assert "时间格式" in data["message"]

    @pytest.mark.asyncio
    async def test_create_all_day_end_defaults_to_start(
        self, create_tool, mock_service
    ):
        with patch.object(
            create_tool,
            "_get_accessor",
            return_value=_mock_acc(mock_service),
        ):
            await create_tool._arun(
                title="全天事件", start_time="2026-09-20", all_day=True
            )
        kwargs = mock_service.create_event.call_args.kwargs
        assert kwargs["all_day"] is True
        assert kwargs["end_time"] == kwargs["start_time"]


# ========== ListCalendarEventsTool ==========


class TestListCalendarEvents:
    @pytest.mark.asyncio
    async def test_list_today_expands_recurring(self, list_tool, mock_service):
        mock_service.list_events_in_range = AsyncMock(
            return_value=[_make_event(recurring=True)],
        )
        with patch.object(
            list_tool,
            "_get_accessor",
            return_value=_mock_acc(mock_service),
        ):
            result = await list_tool._arun(date="2026-09-20")
        data = json.loads(result)
        assert data["success"] is True
        assert data["count"] >= 1
        assert data["events"][0]["is_recurring_instance"] is True

    @pytest.mark.asyncio
    async def test_list_invalid_date(self, list_tool):
        result = await list_tool._arun(date="not-a-date")
        data = json.loads(result)
        assert data["success"] is False


# ========== UpdateCalendarEventTool ==========


class TestUpdateCalendarEvent:
    @pytest.mark.asyncio
    async def test_update_success(self, update_tool, mock_service):
        with patch.object(
            update_tool,
            "_get_accessor",
            return_value=_mock_acc(mock_service),
        ):
            result = await update_tool._arun(event_id=1, title="改名")
        data = json.loads(result)
        assert data["success"] is True
        assert data["action"] == "updated"
        mock_service.update_event.assert_called_once_with(
            1,
            {"title": "改名"},
        )


# ========== DeleteCalendarEventTool ==========


class TestDeleteCalendarEvent:
    @pytest.mark.asyncio
    async def test_delete_success(self, delete_tool, mock_service):
        with patch.object(
            delete_tool,
            "_get_accessor",
            return_value=_mock_acc(mock_service),
        ):
            result = await delete_tool._arun(event_id=1)
        data = json.loads(result)
        assert data["success"] is True
        mock_service.delete_event.assert_called_once_with(1)
