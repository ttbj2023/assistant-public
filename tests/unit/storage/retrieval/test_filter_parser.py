"""过滤器解析器单元测试.

测试 src.storage.retrieval.filter_parser 模块的过滤器解析逻辑.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.storage.retrieval.filter_parser import FilterParser


class TestTimeFilterParsing:
    """时间过滤器解析测试类."""

    # ========== 相对时间测试 ==========

    def test_parse_time_filter_yesterday_should_return_yesterday_range(self):
        """测试时间解析：yesterday应返回昨天的时间范围"""
        # Arrange
        time_filter = "yesterday"
        now = datetime.now(UTC)

        # Act
        start, end = FilterParser.parse_time_filter(time_filter)

        # Assert
        assert start is not None
        assert end is not None
        # 验证开始时间是昨天0点
        expected_start = (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        assert start == expected_start
        # 验证结束时间是今天0点（即昨天结束）
        expected_end = expected_start + timedelta(days=1)
        assert end == expected_end

    def test_parse_time_filter_today_should_return_today_range(self):
        """测试时间解析：today应返回今天的时间范围"""
        # Arrange
        time_filter = "today"
        now = datetime.now(UTC)

        # Act
        start, end = FilterParser.parse_time_filter(time_filter)

        # Assert
        assert start is not None
        assert end is not None
        expected_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        assert start == expected_start
        expected_end = expected_start + timedelta(days=1)
        assert end == expected_end

    def test_parse_time_filter_tomorrow_should_return_tomorrow_range(self):
        """测试时间解析：tomorrow应返回明天的时间范围"""
        # Arrange
        time_filter = "tomorrow"
        now = datetime.now(UTC)

        # Act
        start, end = FilterParser.parse_time_filter(time_filter)

        # Assert
        assert start is not None
        assert end is not None
        expected_start = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        assert start == expected_start
        expected_end = expected_start + timedelta(days=1)
        assert end == expected_end

    def test_parse_time_filter_this_week_should_return_week_range(self):
        """测试时间解析：this_week应返回本周的时间范围"""
        # Arrange
        time_filter = "this_week"
        now = datetime.now(UTC)

        # Act
        start, end = FilterParser.parse_time_filter(time_filter)

        # Assert
        assert start is not None
        assert end is not None
        # 验证是周一
        assert start.weekday() == 0  # Monday
        assert start.hour == 0
        assert start.minute == 0
        # 验证结束是下周一
        assert end == start + timedelta(weeks=1)

    def test_parse_time_filter_last_week_should_return_last_week_range(self):
        """测试时间解析：last_week应返回上周的时间范围"""
        # Arrange
        time_filter = "last_week"

        # Act
        start, end = FilterParser.parse_time_filter(time_filter)

        # Assert
        assert start is not None
        assert end is not None
        assert start.weekday() == 0  # Monday
        assert end == start + timedelta(weeks=1)

    def test_parse_time_filter_last_N_days_should_return_correct_range(self):
        """测试时间解析：last_N_days应返回正确的范围"""
        # Arrange
        time_filter = "last_7_days"
        now = datetime.now(UTC)

        # Act
        start, end = FilterParser.parse_time_filter(time_filter)

        # Assert
        assert start is not None
        assert end is not None
        # 验证开始时间是7天前
        expected_start = now - timedelta(days=7)
        assert start.date() == expected_start.date()
        # 验证结束时间是现在
        assert end.date() == now.date()

    def test_parse_time_filter_last_N_hours_should_return_correct_range(self):
        """测试时间解析：last_N_hours应返回正确的范围"""
        # Arrange
        time_filter = "last_24_hours"
        now = datetime.now(UTC)

        # Act
        start, end = FilterParser.parse_time_filter(time_filter)

        # Assert
        assert start is not None
        assert end is not None
        # 验证开始时间是24小时前
        expected_start = now - timedelta(hours=24)
        time_diff = (now - start).total_seconds()
        assert abs(time_diff - 24 * 3600) < 2  # 允许2秒误差
        # 验证结束时间是现在
        assert end.date() == now.date()

    # ========== 绝对时间测试 ==========

    def test_parse_time_filter_exact_date_should_return_that_date(self):
        """测试时间解析：精确日期应返回该日期的范围"""
        # Arrange
        time_filter = "2024-01-15"

        # Act
        start, end = FilterParser.parse_time_filter(time_filter)

        # Assert
        assert start is not None
        assert end is not None
        assert start.year == 2024
        assert start.month == 1
        assert start.day == 15
        assert start.hour == 0
        # 结束应该是第二天
        assert end.day == 16

    def test_parse_time_filter_date_range_should_return_range(self):
        """测试时间解析：日期范围应返回正确范围"""
        # Arrange
        time_filter = "2024-01-01_to_2024-01-31"

        # Act
        start, end = FilterParser.parse_time_filter(time_filter)

        # Assert
        assert start is not None
        assert end is not None
        assert start.year == 2024
        assert start.month == 1
        assert start.day == 1
        assert end.year == 2024
        assert end.month == 2  # 下个月1号
        assert end.day == 1

    # ========== 边界条件测试 ==========

    def test_parse_time_filter_empty_should_return_none(self):
        """测试时间解析：空字符串应返回None"""
        # Arrange
        time_filter = ""

        # Act
        start, end = FilterParser.parse_time_filter(time_filter)

        # Assert
        assert start is None
        assert end is None

    def test_parse_time_filter_none_should_return_none(self):
        """测试时间解析：None应返回None"""
        # Arrange
        time_filter = None

        # Act
        start, end = FilterParser.parse_time_filter(time_filter)

        # Assert
        assert start is None
        assert end is None

    def test_parse_time_filter_whitespace_should_return_none(self):
        """测试时间解析：纯空格应返回None"""
        # Arrange
        time_filter = "   "

        # Act
        start, end = FilterParser.parse_time_filter(time_filter)

        # Assert
        assert start is None
        assert end is None

    def test_parse_time_filter_invalid_format_should_return_none(self):
        """测试时间解析：无效格式应返回None"""
        # Arrange
        time_filter = "invalid_time_format"

        # Act
        start, end = FilterParser.parse_time_filter(time_filter)

        # Assert
        # 根据实现，返回None作为降级处理
        assert start is None
        assert end is None


class TestParseFilters:
    """parse_filters 综合解析测试类."""

    def test_parse_filters_with_valid_time_filter(self):
        """测试综合解析：有效时间过滤器应返回包含 time_range 的字典"""
        # Arrange
        time_filter = "last_7_days"

        # Act
        result = FilterParser.parse_filters(time_filter=time_filter)

        # Assert
        assert isinstance(result, dict)
        assert "time_range" in result
        assert result["time_range"][0] is not None
        assert result["time_range"][1] is not None

    def test_parse_filters_with_empty_filter(self):
        """测试综合解析：空过滤器应返回空字典"""
        # Arrange & Act
        result = FilterParser.parse_filters(time_filter="")

        # Assert
        assert isinstance(result, dict)
        assert len(result) == 0

    def test_parse_filters_with_exact_date(self):
        """测试综合解析：精确日期应正常解析"""
        # Arrange
        time_filter = "2024-01-15"

        # Act
        result = FilterParser.parse_filters(time_filter=time_filter)

        # Assert
        assert "time_range" in result
        assert result["time_range"][0].year == 2024
        assert result["time_range"][0].month == 1
        assert result["time_range"][0].day == 15
