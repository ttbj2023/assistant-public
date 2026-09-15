"""RFC 5545 ICS 文本生成 (纯函数).

规则要点:
- 文本转义: 反斜杠/分号/逗号/换行, 转义顺序必须先处理反斜杠
- 折行: 每行 ≤75 octets, 续行以单个空格开头, 不切断多字节 UTF-8 字符
- 定时事件输出 UTC (Z 后缀); 全天事件输出 DATE, DTEND 为存储结束日 +1 天
  (存储 inclusive → RFC exclusive)
- CRLF 行结尾
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.storage.models.calendar_event import CalendarEvent, CalendarEventStatus

_PROD_ID = "-//Assistant//Calendar 1.0//CN"
_LINE_OCTET_LIMIT = 75


def escape_ics_text(text: str | None) -> str:
    """转义 ICS 文本值中的特殊字符.

    Args:
        text: 原始文本

    Returns:
        转义后的文本

    """
    if not text:
        return ""
    return (
        text
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def fold_ics_line(line: str) -> str:
    """按 75 octets 折行, 续行以空格开头.

    以字节为单位累计, 遇到会超限的多字节字符时整体移入续行,
    保证不切断 UTF-8 字符.

    Args:
        line: 未折行的完整属性行

    Returns:
        CRLF 连接的折行结果

    """
    if len(line.encode("utf-8")) <= _LINE_OCTET_LIMIT:
        return line

    segments: list[str] = []
    current = ""
    current_octets = 0

    for char in line:
        char_octets = len(char.encode("utf-8"))
        # 续行首的空格占 1 octet, 首行无前缀; 统一按 +1 预留
        if current_octets + char_octets > _LINE_OCTET_LIMIT - 1 and current:
            segments.append(current)
            current = char
            current_octets = char_octets
        else:
            current += char
            current_octets += char_octets

    if current:
        segments.append(current)

    return "\r\n ".join(segments)


def format_ics_datetime_utc(dt: datetime) -> str:
    """格式化为 UTC datetime 值 (YYYYMMDDTHHMMSSZ). naive 输入视为 UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def format_ics_date(dt: datetime) -> str:
    """格式化为 DATE 值 (YYYYMMDD)."""
    return dt.strftime("%Y%m%d")


def build_event_block(event: CalendarEvent) -> str:
    """生成单个 VEVENT 块.

    Args:
        event: 日程事件

    Returns:
        CRLF 结尾的 VEVENT 文本块

    """
    lines: list[str] = [
        "BEGIN:VEVENT",
        f"UID:{event.event_uid}@assistant",
        "DTSTAMP:"
        + format_ics_datetime_utc(
            event.updated_at or event.created_at or datetime.now(UTC),
        ),
    ]

    if event.all_day:
        lines.append(f"DTSTART;VALUE=DATE:{format_ics_date(event.start_time)}")
        # 存储 inclusive 结束日 → RFC exclusive, +1 天; start==end 表示当天事件
        end_base = (
            event.end_time if event.end_time > event.start_time else event.start_time
        )
        lines.append(
            f"DTEND;VALUE=DATE:{format_ics_date(end_base + timedelta(days=1))}",
        )
    else:
        lines.append(f"DTSTART:{format_ics_datetime_utc(event.start_time)}")
        lines.append(f"DTEND:{format_ics_datetime_utc(event.end_time)}")

    if event.recurrence_rule:
        rrule = event.recurrence_rule
        if event.recurrence_until is not None:
            until_value = (
                format_ics_date(event.recurrence_until)
                if event.all_day
                else format_ics_datetime_utc(event.recurrence_until)
            )
            rrule = f"{rrule};UNTIL={until_value}"
        lines.append(f"RRULE:{rrule}")

    if event.status == CalendarEventStatus.CANCELLED:
        lines.append("STATUS:CANCELLED")

    lines.append(f"SUMMARY:{escape_ics_text(event.title)}")
    if event.location:
        lines.append(f"LOCATION:{escape_ics_text(event.location)}")
    if event.description:
        lines.append(f"DESCRIPTION:{escape_ics_text(event.description)}")

    lines.append("END:VEVENT")
    return "\r\n".join(fold_ics_line(line) for line in lines) + "\r\n"


def build_calendar_feed(events: list[CalendarEvent], *, calendar_name: str) -> str:
    """生成完整 VCALENDAR 订阅内容.

    Args:
        events: 日程事件列表
        calendar_name: 日历名 (X-WR-CALNAME, 手机端显示)

    Returns:
        完整 ICS 文本

    """
    header = [
        "BEGIN:VCALENDAR",
        f"PRODID:{_PROD_ID}",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_ics_text(calendar_name)}",
        "X-PUBLISHED-TTL:PT1H",
    ]
    body = [build_event_block(event) for event in events]
    return "\r\n".join(header) + "\r\n" + "".join(body) + "END:VCALENDAR\r\n"
