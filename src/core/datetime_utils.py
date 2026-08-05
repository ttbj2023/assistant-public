"""统一时间工具 - 内部一律 aware UTC, 展示层按用户时区换算.

约定:
- now_utc(): 存储/缓存/逻辑的唯一当前时间来源 (aware UTC)
- naive DateTime 列绑定时 SQLAlchemy 自动剥 tz (保留 UTC 墙钟),
  读出按 naive UTC 解释, 与 now_utc() 产出在 SQL 比较层一致
- to_user_tz(): 展示层换算, naive 输入视为 UTC

用户时区来源: auth_manager.get_user_timezone() (默认 Asia/Shanghai).
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

_DEFAULT_TZ = "Asia/Shanghai"


def now_utc() -> datetime:
    """当前时刻, aware UTC. 内部时间的唯一来源."""
    return datetime.now(UTC)


def to_user_tz(dt: datetime, timezone: str = _DEFAULT_TZ) -> datetime:
    """将 UTC datetime 换算为用户时区. naive 输入视为 UTC.

    Args:
        dt: 待换算的 datetime (aware UTC 或 naive; naive 视为 UTC)
        timezone: 目标时区名 (IANA, 如 "Asia/Shanghai")

    Returns:
        换算为目标时区的 aware datetime
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ZoneInfo(timezone))


__all__ = ["now_utc", "to_user_tz"]
