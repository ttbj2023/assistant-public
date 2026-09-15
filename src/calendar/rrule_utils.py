"""重复规则 (RRULE) 的构建与服务端展开.

存储端只保留规范化 RRULE 字符串 + 独立 recurrence_until;
服务端展开用 dateutil.rrule, 供 "今天有什么日程" 类查询使用,
ICS 输出直接透传 RRULE 由手机端自行展开 (零成本).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dateutil.rrule import rrulestr

from src.calendar.ics_builder import format_ics_datetime_utc
from src.storage.models.calendar_event import CalendarEvent

# 单事件展开上限, 防御无 UNTIL 的永久规则在超大窗口下失控
MAX_EXPANSION_LIMIT = 500

_SUPPORTED_FREQ = ("daily", "weekly", "monthly", "yearly")


def _ensure_aware_utc(dt: datetime) -> datetime:
    """SQLite 读出为 naive UTC, 统一补 tz 后再与 aware 窗口比较."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def build_rrule(
    freq: str,
    interval: int = 1,
) -> str:
    """构建规范化 RRULE 字符串.

    UNTIL 由独立字段 recurrence_until 承载, ICS 生成/服务端展开时合并.

    Args:
        freq: 重复频率 (daily/weekly/monthly/yearly)
        interval: 间隔倍数, 如 interval=2 的 weekly = 每两周

    Returns:
        如 "FREQ=WEEKLY;INTERVAL=2" 的 RRULE 字符串

    Raises:
        ValueError: 不支持的频率或非法间隔

    """
    normalized = freq.strip().lower()
    if normalized not in _SUPPORTED_FREQ:
        raise ValueError(f"不支持的重复频率: {freq}, 支持: {_SUPPORTED_FREQ}")
    if interval < 1:
        raise ValueError("间隔必须 >= 1")
    return f"FREQ={normalized.upper()};INTERVAL={interval}"


def expand_occurrences(
    event: CalendarEvent,
    window_start: datetime,
    window_end: datetime,
    limit: int = MAX_EXPANSION_LIMIT,
) -> list[datetime]:
    """展开事件在窗口内的出现时刻 (occurrence 开始时间).

    Args:
        event: 日程事件
        window_start: 窗口起点
        window_end: 窗口终点
        limit: 展开上限

    Returns:
        升序的出现时刻列表

    """
    if not event.recurrence_rule:
        # 单次事件: 开始时刻落在窗口内即出现
        event_start = _ensure_aware_utc(event.start_time)
        if window_start <= event_start <= window_end:
            return [event_start]
        return []

    rule_str = event.recurrence_rule
    if event.recurrence_until is not None:
        until_value = format_ics_datetime_utc(event.recurrence_until)
        rule_str = f"{rule_str};UNTIL={until_value}"

    rule = rrulestr(rule_str, dtstart=_ensure_aware_utc(event.start_time))
    occurrences: list[datetime] = []
    for dt in rule:
        if dt > window_end:
            break
        if dt >= window_start:
            occurrences.append(dt)
            if len(occurrences) >= limit:
                break
    return occurrences


def expand_events_in_range(
    events: list[CalendarEvent],
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, Any]]:
    """把事件列表展开为窗口内的日程实例 (按开始时间排序).

    重复事件的每个实例保持原事件时长, 标记 is_recurring_instance.

    Args:
        events: 原始事件列表 (通常来自 list_events_in_range)
        window_start: 窗口起点
        window_end: 窗口终点

    Returns:
        排序后的实例字典列表

    """
    items: list[dict[str, Any]] = []
    for event in events:
        duration = event.end_time - event.start_time
        for occ_start in expand_occurrences(event, window_start, window_end):
            items.append(
                {
                    "event_id": event.id,
                    "event_uid": event.event_uid,
                    "title": event.title,
                    "description": event.description,
                    "location": event.location,
                    "all_day": event.all_day,
                    "start_time": _ensure_aware_utc(occ_start),
                    "end_time": _ensure_aware_utc(occ_start + duration),
                    "is_recurring_instance": bool(event.recurrence_rule),
                    "recurrence_rule": event.recurrence_rule,
                },
            )
    items.sort(key=lambda item: item["start_time"])
    return items


__all__ = [
    "MAX_EXPANSION_LIMIT",
    "build_rrule",
    "expand_events_in_range",
    "expand_occurrences",
]
