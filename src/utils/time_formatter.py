"""时间格式化工具 - 统一时间处理逻辑.

提供统一的时间格式化接口,支持多种时间格式输入和标准化输出.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def format_timestamp(timestamp: Any) -> str:
    """格式化时间戳为统一格式 (YYYY-MM-DD HH:MM).

    支持多种时间格式输入: ISO字符串,标准格式字符串,datetime对象等.
    """
    if not timestamp:
        return ""

    try:
        if isinstance(timestamp, str):
            if "T" in timestamp:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(timestamp[:19], "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%Y-%m-%d %H:%M")

        if hasattr(timestamp, "strftime"):
            return timestamp.strftime("%Y-%m-%d %H:%M")

        time_str = str(timestamp)
        return time_str[:16] if len(time_str) > 16 else time_str

    except Exception as e:
        logger.warning("时间戳格式化失败: %s, 错误: %s", timestamp, e)
        return str(timestamp)[:16] if timestamp else ""


def format_date_short(timestamp: Any) -> str:
    """格式化日期为短格式 (YYYY-MM-DD)."""
    if not timestamp:
        return ""

    try:
        if isinstance(timestamp, str):
            if "T" in timestamp:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(timestamp[:19], "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%Y-%m-%d")

        if hasattr(timestamp, "strftime"):
            return timestamp.strftime("%Y-%m-%d")

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
