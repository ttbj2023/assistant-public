"""时间格式化工具测试.

测试 src.utils.time_formatter 模块中的时间格式化功能。
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from src.utils.time_formatter import (
    format_date_short,
    format_due_date_short,
    format_timestamp,
)


class TestTimeFormatter:
    """时间格式化工具测试类."""

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            (None, ""),
            ("", ""),
            (datetime(2024, 1, 15, 14, 30, 45), "2024-01-15 14:30"),
            ("2024-01-15T14:30:45Z", "2024-01-15 14:30"),
            ("2024-01-15T14:30:45+08:00", "2024-01-15 14:30"),
            ("2024-01-15 14:30:45", "2024-01-15 14:30"),
            ("2024-01-15", "2024-01-15"),
            (datetime(2024, 1, 15, 14, 30, 45, tzinfo=UTC), "2024-01-15 14:30"),
        ],
    )
    def test_format_timestamp_should_handle_various_inputs(self, input_val, expected):
        result = format_timestamp(input_val)
        assert result == expected

    def test_format_timestamp_exception_handling(self):
        problematic_string = "2024-13-45T99:99:99Z"

        with patch("src.utils.time_formatter.logger") as mock_logger:
            result = format_timestamp(problematic_string)
            assert len(result) <= 16
            mock_logger.warning.assert_called_once()

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            (None, ""),
            ("", ""),
            (datetime(2024, 1, 15, 14, 30, 45), "2024-01-15"),
            ("2024-01-15T14:30:45Z", "2024-01-15"),
        ],
    )
    def test_format_date_short_should_handle_various_inputs(self, input_val, expected):
        result = format_date_short(input_val)
        assert result == expected

    def test_format_due_date_short_same_as_date_should_work_when_short(self):
        assert format_due_date_short("2024-01-15T14:30:45Z") == "2024-01-15"
        assert format_due_date_short(datetime(2024, 1, 15, 14, 30, 45)) == "2024-01-15"
        assert format_due_date_short(None) == ""
        assert format_due_date_short("") == ""

    def test_format_timestamp_should_produce_correct_format(self):
        assert format_timestamp("2024-01-15T14:30:45Z") == "2024-01-15 14:30"
        assert format_timestamp(datetime(2024, 1, 15, 14, 30, 45)) == "2024-01-15 14:30"
        assert format_date_short("2024-01-15T14:30:45Z") == "2024-01-15"


class TestUserTimezoneConversion:
    """UserContext 设置时, format_* 应换算到用户时区; 无 context 返回 UTC."""

    def test_format_date_short_converts_to_user_tz(self):
        from src.core.context import (
            UserContext,
            reset_user_context,
            set_user_context,
        )

        # UTC 2024-01-15 02:00 -> America/New_York (UTC-5) = 2024-01-14 21:00
        token = set_user_context(
            UserContext(
                user_id="u",
                thread_id="t",
                agent_id="a",
                timezone="America/New_York",
            ),
        )
        try:
            assert format_date_short(datetime(2024, 1, 15, 2, 0, tzinfo=UTC)) == "2024-01-14"
        finally:
            reset_user_context(token)

    def test_format_date_short_no_context_returns_utc(self):
        # 无 context -> 不换算, 返回 UTC 原值
        assert format_date_short(datetime(2024, 1, 15, 2, 0, tzinfo=UTC)) == "2024-01-15"

    def test_format_timestamp_converts_to_user_tz(self):
        from src.core.context import (
            UserContext,
            reset_user_context,
            set_user_context,
        )

        token = set_user_context(
            UserContext(
                user_id="u",
                thread_id="t",
                agent_id="a",
                timezone="America/New_York",
            ),
        )
        try:
            # UTC 14:30 -> New_York 09:30
            assert format_timestamp(datetime(2024, 1, 15, 14, 30, tzinfo=UTC)) == "2024-01-15 09:30"
        finally:
            reset_user_context(token)
