"""ics_builder 单元测试.

验证 RFC 5545 文本生成: 转义 / 折行 / 日期时间格式 / 全天事件 / RRULE 透传.
纯函数测试, 无外部依赖.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.calendar.ics_builder import (
    build_calendar_feed,
    escape_ics_text,
    fold_ics_line,
    format_ics_date,
    format_ics_datetime_utc,
)


def _make_event(**overrides):
    """构造测试用 CalendarEvent."""
    from src.storage.models.calendar_event import CalendarEvent

    defaults = {
        "title": "团队周会",
        "start_time": datetime(2026, 9, 20, 2, 0, tzinfo=UTC),
        "end_time": datetime(2026, 9, 20, 3, 0, tzinfo=UTC),
        "user_id": "user1",
    }
    defaults.update(overrides)
    return CalendarEvent(**defaults)


class TestEscapeIcsText:
    """RFC 5545 文本转义."""

    def test_escapes_special_chars(self) -> None:
        assert escape_ics_text("a;b,c") == r"a\;b\,c"

    def test_escapes_backslash_first(self) -> None:
        assert escape_ics_text(r"a\b") == r"a\\b"

    def test_escapes_newline(self) -> None:
        assert escape_ics_text("a\nb") == r"a\nb"

    def test_plain_text_unchanged(self) -> None:
        assert escape_ics_text("普通文本 text-1_2") == "普通文本 text-1_2"

    def test_none_returns_empty(self) -> None:
        assert escape_ics_text(None) == ""


class TestFoldIcsLine:
    """RFC 5545 折行 (75 octets, 不切断多字节字符)."""

    def test_short_line_unchanged(self) -> None:
        line = "SUMMARY:短标题"
        assert fold_ics_line(line) == line

    def test_long_line_folded_with_leading_space(self) -> None:
        line = "SUMMARY:" + "长" * 50
        folded = fold_ics_line(line)
        continuation_lines = folded.split("\r\n ")[1:]
        assert continuation_lines, "应存在续行"
        # 每行 (含首行) 编码后不超过 75 octets
        for raw in folded.split("\r\n"):
            content = raw[1:] if raw.startswith(" ") else raw
            assert len(content.encode("utf-8")) <= 75

    def test_multibyte_char_not_split(self) -> None:
        """续行边界不得落在多字节 UTF-8 字符中间."""
        line = "DESCRIPTION:" + "描述内容" * 40
        folded = fold_ics_line(line)
        # 解码回原文验证无损
        joined = folded.replace("\r\n ", "")
        assert joined == line


class TestDatetimeFormats:
    """日期时间格式化."""

    def test_format_ics_datetime_utc(self) -> None:
        dt = datetime(2026, 9, 20, 2, 30, 0, tzinfo=UTC)
        assert format_ics_datetime_utc(dt) == "20260920T023000Z"

    def test_format_ics_datetime_naive_treated_as_utc(self) -> None:
        dt = datetime(2026, 9, 20, 2, 30, 0)
        assert format_ics_datetime_utc(dt) == "20260920T023000Z"

    def test_format_ics_datetime_converts_aware_to_utc(self) -> None:
        from zoneinfo import ZoneInfo

        dt = datetime(2026, 9, 20, 10, 30, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert format_ics_datetime_utc(dt) == "20260920T023000Z"

    def test_format_ics_date(self) -> None:
        dt = datetime(2026, 9, 20, 15, 0, tzinfo=UTC)
        assert format_ics_date(dt) == "20260920"


class TestBuildEventBlock:
    """VEVENT 块生成."""

    def test_timed_event_basic_fields(self) -> None:
        from src.calendar.ics_builder import build_event_block

        event = _make_event(location="会议室A")
        block = build_event_block(event)
        assert "BEGIN:VEVENT" in block and "END:VEVENT" in block
        assert f"UID:{event.event_uid}@assistant" in block
        assert "DTSTART:20260920T020000Z" in block
        assert "DTEND:20260920T030000Z" in block
        assert "SUMMARY:团队周会" in block
        assert "LOCATION:会议室A" in block
        assert "RRULE:" not in block

    def test_all_day_event_uses_date_value_and_exclusive_end(self) -> None:
        """全天事件: DATE 格式 + DTEND 为存储结束日 +1 天 (inclusive→exclusive)."""
        from src.calendar.ics_builder import build_event_block

        start = datetime(2026, 9, 20, tzinfo=UTC)
        event = _make_event(
            all_day=True,
            start_time=start,
            end_time=start + timedelta(days=2),
        )
        block = build_event_block(event)
        assert "DTSTART;VALUE=DATE:20260920" in block
        assert "DTEND;VALUE=DATE:20260923" in block

    def test_recurring_event_outputs_rrule(self) -> None:
        from src.calendar.ics_builder import build_event_block

        start = datetime(2026, 9, 20, 2, 0, tzinfo=UTC)
        event = _make_event(
            start_time=start,
            end_time=start + timedelta(hours=1),
            recurrence_rule="FREQ=WEEKLY;INTERVAL=1",
        )
        block = build_event_block(event)
        assert "RRULE:FREQ=WEEKLY;INTERVAL=1" in block

    def test_recurring_event_appends_until(self) -> None:
        """recurrence_until 应合并进 RRULE 的 UNTIL 子句 (UTC)."""
        from src.calendar.ics_builder import build_event_block

        start = datetime(2026, 9, 20, 2, 0, tzinfo=UTC)
        event = _make_event(
            start_time=start,
            end_time=start + timedelta(hours=1),
            recurrence_rule="FREQ=DAILY",
            recurrence_until=datetime(2026, 12, 31, tzinfo=UTC),
        )
        block = build_event_block(event)
        assert "RRULE:FREQ=DAILY;UNTIL=20261231T000000Z" in block

    def test_all_day_recurring_until_uses_date(self) -> None:
        """全天重复事件的 UNTIL 用 DATE 格式."""
        from src.calendar.ics_builder import build_event_block

        start = datetime(2026, 9, 20, tzinfo=UTC)
        event = _make_event(
            all_day=True,
            start_time=start,
            end_time=start,
            recurrence_rule="FREQ=WEEKLY",
            recurrence_until=datetime(2026, 12, 31, tzinfo=UTC),
        )
        block = build_event_block(event)
        assert "RRULE:FREQ=WEEKLY;UNTIL=20261231" in block

    def test_cancelled_status_output(self) -> None:
        from src.calendar.ics_builder import build_event_block
        from src.storage.models.calendar_event import CalendarEventStatus

        event = _make_event(status=CalendarEventStatus.CANCELLED)
        block = build_event_block(event)
        assert "STATUS:CANCELLED" in block


class TestBuildCalendarFeed:
    """VCALENDAR 整体输出."""

    def test_feed_structure(self) -> None:
        events = [_make_event(), _make_event(title="第二场")]
        feed = build_calendar_feed(events, calendar_name="我的日程")
        assert feed.startswith("BEGIN:VCALENDAR")
        assert feed.endswith("END:VCALENDAR\r\n")
        assert "VERSION:2.0" in feed
        assert "PRODID:" in feed
        assert "X-WR-CALNAME:我的日程" in feed
        assert "X-PUBLISHED-TTL:PT1H" in feed
        assert feed.count("BEGIN:VEVENT") == 2

    def test_feed_uses_crlf_line_endings(self) -> None:
        feed = build_calendar_feed([_make_event()], calendar_name="cal")
        assert "\r\n" in feed
        assert "\n" not in feed.replace("\r\n", "")

    def test_empty_feed_still_valid(self) -> None:
        feed = build_calendar_feed([], calendar_name="cal")
        assert "BEGIN:VCALENDAR" in feed
        assert "BEGIN:VEVENT" not in feed
