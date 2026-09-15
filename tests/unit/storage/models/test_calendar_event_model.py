"""CalendarEvent 数据模型单元测试.

验证字段验证器 (title / 时间对) 与 event_uid 默认生成行为.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.storage.models.calendar_event import CalendarEvent, CalendarEventStatus


def _valid_times() -> tuple[datetime, datetime]:
    start = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    return start, start + timedelta(hours=1)


class TestCalendarEventTitleValidation:
    """CalendarEvent.title 字段验证器测试."""

    def test_empty_title_raises(self) -> None:
        start, end = _valid_times()
        with pytest.raises(ValidationError, match="日程标题不能为空"):
            CalendarEvent(title="", start_time=start, end_time=end, user_id="u")

    def test_title_too_long_raises(self) -> None:
        start, end = _valid_times()
        with pytest.raises(ValidationError, match="日程标题不能超过200个字符"):
            CalendarEvent(
                title="x" * 201,
                start_time=start,
                end_time=end,
                user_id="u",
            )

    def test_valid_title_returns_stripped(self) -> None:
        start, end = _valid_times()
        event = CalendarEvent(
            title="  团队周会  ",
            start_time=start,
            end_time=end,
            user_id="u",
        )
        assert event.title == "团队周会"


class TestCalendarEventTimeValidation:
    """CalendarEvent 时间对验证器测试."""

    def test_end_before_start_raises(self) -> None:
        start, end = _valid_times()
        with pytest.raises(ValidationError, match="结束时间不能早于开始时间"):
            CalendarEvent(
                title="测试",
                start_time=end,
                end_time=start,
                user_id="u",
            )

    def test_end_equal_start_allowed_for_all_day(self) -> None:
        """全天事件 start == end (inclusive 结束日语义) 应允许."""
        day = datetime(2026, 9, 20, tzinfo=UTC)
        event = CalendarEvent(
            title="全天事件",
            start_time=day,
            end_time=day,
            all_day=True,
            user_id="u",
        )
        assert event.start_time == event.end_time


class TestCalendarEventUid:
    """CalendarEvent.event_uid 默认生成测试."""

    def test_default_uid_generated(self) -> None:
        start, end = _valid_times()
        event = CalendarEvent(
            title="测试",
            start_time=start,
            end_time=end,
            user_id="u",
        )
        assert event.event_uid.startswith("evt_")
        assert len(event.event_uid) == len("evt_") + 8

    def test_explicit_uid_preserved(self) -> None:
        start, end = _valid_times()
        event = CalendarEvent(
            title="测试",
            event_uid="evt_custom01",
            start_time=start,
            end_time=end,
            user_id="u",
        )
        assert event.event_uid == "evt_custom01"


class TestCalendarEventDefaults:
    """CalendarEvent 默认值测试."""

    def test_defaults(self) -> None:
        start, end = _valid_times()
        event = CalendarEvent(
            title="测试",
            start_time=start,
            end_time=end,
            user_id="u",
        )
        assert event.all_day is False
        assert event.status == CalendarEventStatus.ACTIVE
        assert event.recurrence_rule is None
        assert event.recurrence_until is None
        assert event.location is None
        assert event.description is None

    def test_to_dict_contains_all_fields(self) -> None:
        start, end = _valid_times()
        event = CalendarEvent(
            title="测试",
            start_time=start,
            end_time=end,
            user_id="u",
        )
        data = event.to_dict()
        assert data["title"] == "测试"
        assert data["status"] == "active"
        assert data["start_time"] == start.isoformat()
        assert data["end_time"] == end.isoformat()
        assert "event_uid" in data
        assert "recurrence_rule" in data
