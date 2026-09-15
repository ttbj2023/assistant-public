"""日历工具共享逻辑 - 组合替代继承.

纯函数: parse_event_datetime / parse_recurrence_freq / event_to_dict
Service 访问器: CalendarServiceAccessor (带缓存, 用户级服务)

时区规则:
- 定时事件: naive 输入按用户时区解释, 统一转 UTC 存储
- 全天事件: 日期字符串按 UTC 零点存储 (跳过时区转换, ICS DATE 语义)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.calendar.rrule_utils import build_rrule
from src.core.context import get_user_context_or_none
from src.core.datetime_utils import to_user_tz
from src.tools.internal.todo_helpers import json_result

logger = logging.getLogger(__name__)

_DEFAULT_TIMEZONE = "Asia/Shanghai"

_FREQ_ALIASES = {
    "daily": "daily",
    "每天": "daily",
    "每日": "daily",
    "weekly": "weekly",
    "每周": "weekly",
    "monthly": "monthly",
    "每月": "monthly",
    "yearly": "yearly",
    "每年": "yearly",
    "每两周": "weekly",
    "隔周": "weekly",
}


def _get_user_timezone() -> str:
    ctx = get_user_context_or_none()
    if ctx is not None and ctx.timezone:
        return ctx.timezone
    return _DEFAULT_TIMEZONE


def parse_event_datetime(
    dt_str: str,
    *,
    timezone: str,
    all_day: bool = False,
) -> datetime:
    """解析时间字符串为 aware UTC.

    Args:
        dt_str: ISO 格式时间或日期字符串
        timezone: 用户时区 (naive 输入的解释基准)
        all_day: 全天事件按 UTC 零点存储日期

    Returns:
        aware UTC datetime

    Raises:
        ValueError: 格式非法

    """
    normalized = dt_str.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as e:
        raise ValueError(
            f"时间格式无效: '{dt_str}', 请使用 ISO 格式如 2026-09-20T10:00:00"
        ) from e

    if all_day:
        # 全天事件: 只保留日期, UTC 零点 (ICS DATE 输出不受时区影响)
        return parsed.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC)

    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)
    # naive 按用户时区解释
    local = parsed.replace(tzinfo=ZoneInfo(timezone))
    return local.astimezone(UTC)


def parse_recurrence_freq(
    freq: str | None,
    interval: int = 1,
) -> str | None:
    """解析重复频率 (中英文) 为规范化 RRULE 字符串.

    Args:
        freq: daily/weekly/monthly/yearly 或中文别名
        interval: 间隔倍数

    Returns:
        RRULE 字符串, freq 为空返回 None

    Raises:
        ValueError: 不支持的频率

    """
    if not freq:
        return None
    normalized = _FREQ_ALIASES.get(freq.strip().lower())
    if normalized is None:
        raise ValueError(
            f"不支持的重复频率: '{freq}', 支持 daily/weekly/monthly/yearly (或 每天/每周/每月/每年)",
        )
    return build_rrule(normalized, interval=interval)


def parse_date_only(date_str: str, *, timezone: str) -> datetime:
    """解析日期字符串为该用户时区的当天起点 (aware UTC)."""
    try:
        naive = datetime.fromisoformat(date_str.strip())
    except ValueError as e:
        raise ValueError(f"日期格式无效: '{date_str}', 请使用如 2026-09-20") from e
    local = naive.replace(tzinfo=ZoneInfo(timezone))
    return local.astimezone(UTC)


def event_to_dict(event: Any) -> dict[str, Any]:
    """将 CalendarEvent 转换为字典 (时间换算到用户时区)."""
    timezone = _get_user_timezone()

    def _iso(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return to_user_tz(dt, timezone).isoformat()

    return {
        "id": event.id,
        "event_uid": event.event_uid,
        "title": event.title,
        "description": event.description,
        "location": event.location,
        "all_day": event.all_day,
        "start_time": _iso(event.start_time),
        "end_time": _iso(event.end_time),
        "recurrence_rule": event.recurrence_rule,
        "recurrence_until": _iso(event.recurrence_until),
        "status": event.status.value
        if hasattr(event.status, "value")
        else event.status,
        "created_at": _iso(event.created_at),
        "updated_at": _iso(event.updated_at),
    }


class CalendarServiceAccessor:
    """日历 Service 访问器 (组合, 带缓存; 服务为用户级)."""

    def __init__(self, user_id: str, thread_id: str, agent_id: str) -> None:
        self._user_id = user_id
        self._thread_id = thread_id
        self._agent_id = agent_id
        self._service: Any = None

    async def get_service(self) -> Any:
        """获取日历 Service 实例 (带缓存)."""
        if self._service is not None:
            return self._service
        from src.storage.service import create_calendar_service

        service = await create_calendar_service(self._user_id)
        self._service = service
        return service


__all__ = [
    "CalendarServiceAccessor",
    "event_to_dict",
    "json_result",
    "parse_date_only",
    "parse_event_datetime",
    "parse_recurrence_freq",
]
