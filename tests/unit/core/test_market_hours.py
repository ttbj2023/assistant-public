"""market_hours 交易时段纯函数测试."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.core.market_hours import is_trading_hours, to_cn

CN = ZoneInfo("Asia/Shanghai")


class TestTradingHours:
    def test_morning_session(self):
        # 周一 2026-06-29 10:00 CN
        assert is_trading_hours(datetime(2026, 6, 29, 10, 0, tzinfo=CN)) is True

    def test_afternoon_session(self):
        assert is_trading_hours(datetime(2026, 6, 29, 14, 0, tzinfo=CN)) is True

    def test_lunch_break(self):
        assert is_trading_hours(datetime(2026, 6, 29, 12, 0, tzinfo=CN)) is False

    def test_before_open(self):
        assert is_trading_hours(datetime(2026, 6, 29, 9, 0, tzinfo=CN)) is False

    def test_after_close(self):
        assert is_trading_hours(datetime(2026, 6, 29, 15, 1, tzinfo=CN)) is False

    def test_weekend(self):
        # 周日 2026-06-28
        assert is_trading_hours(datetime(2026, 6, 28, 10, 0, tzinfo=CN)) is False

    def test_boundary_open(self):
        # 9:30 含边界
        assert is_trading_hours(datetime(2026, 6, 29, 9, 30, tzinfo=CN)) is True

    def test_boundary_close(self):
        # 15:00 含边界
        assert is_trading_hours(datetime(2026, 6, 29, 15, 0, tzinfo=CN)) is True


class TestToCn:
    def test_default_now_has_tz(self):
        assert to_cn().tzinfo is not None

    def test_naive_treated_as_utc(self):
        # naive 视为 UTC; UTC 02:00 → CN 10:00
        dt = datetime(2026, 6, 29, 2, 0)
        cn = to_cn(dt)
        assert cn.hour == 10
