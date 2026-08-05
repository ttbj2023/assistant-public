"""时间格式化工具 - 统一时间处理逻辑.

提供统一的时间格式化接口,支持多种时间格式输入和标准化输出.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.core.context import get_user_context_or_none
from src.core.datetime_utils import to_user_tz

logger = logging.getLogger(__name__)


def _apply_user_tz(dt: datetime) -> datetime:
    """若当前异步上下文设置了 UserContext, 将 datetime 换算到用户时区.

    naive 输入视为 UTC (存储约定). 无 context 时不换算 (admin/cron/测试路径).
    """
    ctx = get_user_context_or_none()
    if ctx is None:
        return dt
    return to_user_tz(dt, ctx.timezone)


def format_timestamp(timestamp: Any) -> str:
    """格式化时间戳为统一格式 (YYYY-MM-DD HH:MM).

    支持多种时间格式输入: ISO字符串,标准格式字符串,datetime对象等.
    若 UserContext 已设置, 自动换算到用户时区.
    """
    if not timestamp:
        return ""

    try:
        if isinstance(timestamp, str):
            if "T" in timestamp:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(timestamp[:19], "%Y-%m-%d %H:%M:%S")
            return _apply_user_tz(dt).strftime("%Y-%m-%d %H:%M")

        if hasattr(timestamp, "strftime"):
            return _apply_user_tz(timestamp).strftime("%Y-%m-%d %H:%M")

        time_str = str(timestamp)
        return time_str[:16] if len(time_str) > 16 else time_str

    except Exception as e:
        logger.warning("时间戳格式化失败: %s, 错误: %s", timestamp, e)
        return str(timestamp)[:16] if timestamp else ""


def format_date_short(timestamp: Any) -> str:
    """格式化日期为短格式 (YYYY-MM-DD).

    若 UserContext 已设置, 自动换算到用户时区.
    """
    if not timestamp:
        return ""

    try:
        if isinstance(timestamp, str):
            if "T" in timestamp:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(timestamp[:19], "%Y-%m-%d %H:%M:%S")
            return _apply_user_tz(dt).strftime("%Y-%m-%d")

        if hasattr(timestamp, "strftime"):
            return _apply_user_tz(timestamp).strftime("%Y-%m-%d")

        time_str = str(timestamp)
        return time_str[:10] if len(time_str) > 10 else time_str

    except Exception as e:
        logger.warning("日期格式化失败: %s, 错误: %s", timestamp, e)
        return str(timestamp)[:10] if timestamp else ""


def format_due_date_short(timestamp: Any) -> str:
    """格式化截止日期为短格式 (YYYY-MM-DD).

    专门用于TODO项目的截止日期格式化.
    """
    return format_date_short(timestamp)


__all__ = [
    "format_date_short",
    "format_due_date_short",
    "format_timestamp",
]
