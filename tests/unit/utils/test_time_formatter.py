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
