"""rrule_utils 单元测试.

验证重复规则的构建与服务端展开: 频率/间隔/UNTIL/展开窗口/上限保护.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.calendar.rrule_utils import (
    build_rrule,
    expand_events_in_range,
    expand_occurrences,
)
from src.storage.models.calendar_event import CalendarEvent

_MAX_EXPANSION_GUARD = 500


def _recurring_event(**overrides) -> CalendarEvent:
    start = datetime(2026, 9, 7, 2, 0, tzinfo=UTC)  # 周一
    defaults = {
        "title": "每周例会",
        "start_time": start,
        "end_time": start + timedelta(hours=1),
        "recurrence_rule": "FREQ=WEEKLY;INTERVAL=1",
        "user_id": "user1",
    }
    defaults.update(overrides)
    return CalendarEvent(**defaults)


class TestBuildRrule:
    """友好参数 → RRULE 字符串."""

    def test_daily(self) -> None:
        assert build_rrule("daily") == "FREQ=DAILY;INTERVAL=1"

    def test_weekly_with_interval(self) -> None:
        assert build_rrule("weekly", interval=2) == "FREQ=WEEKLY;INTERVAL=2"

    def test_monthly(self) -> None:
        assert build_rrule("monthly") == "FREQ=MONTHLY;INTERVAL=1"

    def test_invalid_freq_raises(self) -> None:
        with pytest.raises(ValueError, match="不支持的重复频率"):
            build_rrule("hourly")


class TestExpandOccurrences:
    """服务端展开."""

    def test_weekly_expansion_in_window(self) -> None:
        event = _recurring_event()
        window_start = datetime(2026, 9, 1, tzinfo=UTC)
        window_end = datetime(2026, 10, 1, tzinfo=UTC)
        occurrences = expand_occurrences(event, window_start, window_end)
        # 9月内每周一: 9/7, 9/14, 9/21, 9/28
        assert len(occurrences) == 4
        assert occurrences[0] == datetime(2026, 9, 7, 2, 0, tzinfo=UTC)
        assert occurrences[-1] == datetime(2026, 9, 28, 2, 0, tzinfo=UTC)

    def test_until_truncates_expansion(self) -> None:
        event = _recurring_event(recurrence_until=datetime(2026, 9, 20, tzinfo=UTC))
        window_start = datetime(2026, 9, 1, tzinfo=UTC)
        window_end = datetime(2026, 10, 1, tzinfo=UTC)
        occurrences = expand_occurrences(event, window_start, window_end)
        assert len(occurrences) == 2  # 9/7, 9/14 (UNTIL 9/20 之前)

    def test_single_event_returns_its_start(self) -> None:
        event = _recurring_event(recurrence_rule=None)
        window_start = datetime(2026, 9, 1, tzinfo=UTC)
        window_end = datetime(2026, 10, 1, tzinfo=UTC)
        assert expand_occurrences(event, window_start, window_end) == [
            event.start_time,
        ]

    def test_occurrences_outside_window_excluded(self) -> None:
        """单次事件在窗口外不出现."""
        event = _recurring_event(recurrence_rule=None)
        window_start = datetime(2026, 10, 1, tzinfo=UTC)
        window_end = datetime(2026, 11, 1, tzinfo=UTC)
        assert expand_occurrences(event, window_start, window_end) == []

    def test_expansion_limit_guards_infinite_rules(self) -> None:
        """无 UNTIL 的永久规则在超大窗口下受上限保护."""
        event = _recurring_event(recurrence_rule="FREQ=DAILY;INTERVAL=1")
        window_start = datetime(2020, 1, 1, tzinfo=UTC)
        window_end = datetime(2030, 1, 1, tzinfo=UTC)
        occurrences = expand_occurrences(event, window_start, window_end)
        assert len(occurrences) <= _MAX_EXPANSION_GUARD


class TestExpandEventsInRange:
    """事件级展开 (供查询接口)."""

    async def test_mixed_events_expanded_and_sorted(self) -> None:
        recurring = _recurring_event()
        single = _recurring_event(
            title="单次会议",
            recurrence_rule=None,
            start_time=datetime(2026, 9, 10, 5, 0, tzinfo=UTC),
            end_time=datetime(2026, 9, 10, 6, 0, tzinfo=UTC),
        )
        window_start = datetime(2026, 9, 1, tzinfo=UTC)
        window_end = datetime(2026, 9, 15, tzinfo=UTC)

        results = expand_events_in_range(
            [single, recurring],
            window_start,
            window_end,
        )

        # 单次 1 条 + weekly 2 条 (9/7, 9/14), 按开始时间排序
        assert len(results) == 3
        starts = [item["start_time"] for item in results]
        assert starts == sorted(starts)
        assert any(item["title"] == "单次会议" for item in results)

        # 重复实例保持原事件时长
        weekly_items = [item for item in results if item["title"] == "每周例会"]
        first = weekly_items[0]
        assert first["end_time"] - first["start_time"] == timedelta(hours=1)
        assert first["is_recurring_instance"] is True
