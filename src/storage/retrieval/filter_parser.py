"""过滤器解析器 - 解析时间过滤器.

用于将字符串格式的时间过滤器转换为具体的查询条件.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


class FilterParser:
    """过滤器解析器."""

    @staticmethod
    def parse_time_filter(time_filter: str) -> tuple[datetime | None, datetime | None]:
        """解析时间过滤器.

        Args:
            time_filter: 时间过滤器字符串

        Returns:
            (开始时间, 结束时间) 的元组,如果解析失败返回 (None, None)

        """
        if not time_filter or not time_filter.strip():
            return None, None

        time_filter = time_filter.strip().lower()
        now = datetime.now(UTC)

        try:
            # 相对时间处理
            if time_filter == "yesterday":
                start_time = now.replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                ) - timedelta(days=1)
                end_time = start_time + timedelta(days=1)
                return start_time, end_time

            if time_filter == "today":
                start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
                end_time = start_time + timedelta(days=1)
                return start_time, end_time

            if time_filter == "tomorrow":
                start_time = now.replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                ) + timedelta(days=1)
                end_time = start_time + timedelta(days=1)
                return start_time, end_time

            if time_filter == "this_week":
                # 本周开始(周一)
                days_since_monday = now.weekday()
                start_time = (now - timedelta(days=days_since_monday)).replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                end_time = start_time + timedelta(weeks=1)
                return start_time, end_time

            if time_filter == "last_week":
                # 上周开始(周一)
                days_since_monday = now.weekday()
                last_monday = (now - timedelta(days=days_since_monday + 7)).replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                start_time = last_monday
                end_time = last_monday + timedelta(weeks=1)
                return start_time, end_time

            if time_filter.startswith("last_") and time_filter.endswith("_days"):
                # last_N_days 格式
                try:
                    days = int(time_filter[5:-5])
                    start_time = now - timedelta(days=days)
                    end_time = now
                    return start_time, end_time
                except ValueError:
                    logger.warning("无法解析天数: %s", time_filter)
                    return None, None

            elif time_filter.startswith("last_") and time_filter.endswith("_hours"):
                # last_N_hours 格式
                try:
                    hours = int(time_filter[5:-6])
                    start_time = now - timedelta(hours=hours)
                    end_time = now
                    return start_time, end_time
                except ValueError:
                    logger.warning("无法解析小时数: %s", time_filter)
                    return None, None

            # 精确时间处理
            elif "_to_" in time_filter:
                # 时间范围: 2024-01-15_to_2024-01-20
                try:
                    start_str, end_str = time_filter.split("_to_")
                    start_time = datetime.strptime(start_str, "%Y-%m-%d")
                    end_time = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(
                        days=1,
                    )
                    return start_time, end_time
                except ValueError:
                    logger.warning("无法解析时间范围: %s", time_filter)
                    return None, None

            else:
                # 单个日期: 2024-01-15
                try:
                    start_time = datetime.strptime(time_filter, "%Y-%m-%d")
                    end_time = start_time + timedelta(days=1)
                    return start_time, end_time
                except ValueError:
                    logger.warning("无法解析日期: %s", time_filter)
                    return None, None

        except Exception as e:
            logger.error("时间过滤器解析错误: %s", e)
            return None, None

    @staticmethod
    def parse_filters(time_filter: str = "") -> dict[str, Any]:
        """解析时间过滤器, 返回标准 filters 字典.

        Args:
            time_filter: 时间过滤器字符串

        Returns:
            可直接传递给 search_conversations 的 filters 字典

        """
        filters: dict[str, Any] = {}

        if time_filter and time_filter.strip():
            time_range = FilterParser.parse_time_filter(time_filter)
            if time_range[0] is not None:
                filters["time_range"] = time_range

        return filters
