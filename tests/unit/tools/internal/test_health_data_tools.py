"""健康数据工具组单元测试.

覆盖 src/tools/internal/ 下健康数据工具组 (health_data_group) 的 8 个子工具:
- view_health_snapshot / query_daily_health / query_metric_trend / compare_health_periods
- list_workout_records / list_meal_records / view_medical_report / list_shopping_items
以及共享模块 health_data_helpers.

Mock边界:
- Mock HealthDataServiceAccessor._service 为 Mock HealthDataService
- 保留真实参数解析与格式化逻辑
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from src.tools.internal.compare_health_periods_tool import CompareHealthPeriodsTool
from src.tools.internal.list_meal_records_tool import ListMealRecordsTool
from src.tools.internal.list_shopping_items_tool import ListShoppingItemsTool
from src.tools.internal.list_workout_records_tool import ListWorkoutRecordsTool
from src.tools.internal.query_daily_health_tool import QueryDailyHealthTool
from src.tools.internal.query_metric_trend_tool import QueryMetricTrendTool
from src.tools.internal.view_health_snapshot_tool import ViewHealthSnapshotTool
from src.tools.internal.view_medical_report_tool import ViewMedicalReportTool
from src.tools.internal.health_data_helpers import (
    HealthDataServiceAccessor,
    first_day_of_month_n_ago,
    has_value,
    user_today,
)
from src.tools.shared.tool_runtime import inject_identity

# 单日汇总模型用到的全部字段
_SUMMARY_FIELDS = [
    "record_date",
    "steps",
    "active_energy_kcal",
    "basal_energy_kcal",
    "distance_km",
    "apple_exercise_minutes",
    "stand_hours",
    "body_mass_kg",
    "body_fat_pct",
    "muscle_mass_kg",
    "resting_hr_bpm",
    "hrv_ms",
    "vo2_max",
    "avg_hr_bpm",
    "min_hr_bpm",
    "max_hr_bpm",
    "blood_oxygen_pct",
    "wrist_temperature",
    "respiratory_rate",
    "sleep_duration_hours",
    "sleep_efficiency",
    "asleep_minutes",
    "deep_sleep_minutes",
    "rem_sleep_minutes",
    "core_sleep_minutes",
    "awake_minutes",
    "flights_climbed",
    "sunlight_minutes",
    "weight_7d_avg",
    "steps_7d_avg",
    "resting_hr_7d_avg",
    "hrv_7d_avg",
    "sleep_7d_avg",
    "sleep_efficiency_7d_avg",
    "exercise_7d_total",
    "bed_time",
    "wake_time",
]


def make_summary(**overrides) -> Mock:
    """构造默认全 None 的单日汇总 Mock, 按需覆盖字段."""
    s = Mock()
    for f in _SUMMARY_FIELDS:
        setattr(s, f, None)
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


@pytest.fixture
def mock_service():
    """基础 Mock HealthDataService(默认无数据, 测试中按需覆盖)."""
    service = Mock()
    service.get_data_coverage = AsyncMock(
        return_value={
            "daily": {"total": 0, "date_range": {"start": None, "end": None}},
        }
    )
    service.get_daily_summaries = AsyncMock(return_value=[])
    service.get_daily_summary = AsyncMock(return_value=None)
    service.get_metric_history = AsyncMock(return_value=[])
    service.get_weekly_summaries = AsyncMock(return_value=[])
    service.get_workout_history_filtered = AsyncMock(return_value=[])
    service.get_workout_stats = AsyncMock(
        return_value={"status": "error", "total_count": 0}
    )
    service.get_latest_report = AsyncMock(return_value=None)
    service.get_report_detail = AsyncMock(return_value={"status": "no_data"})
    service.get_metric_comparison = AsyncMock(return_value={})
    service.get_weekly_activity_summary = AsyncMock(return_value={"status": "error"})
    service.get_nutrition_summary = AsyncMock(return_value={"status": "no_data"})
    service.get_shopping_list = AsyncMock(return_value=[])
    service.get_nutrition_range = AsyncMock(return_value={})
    return service


def make_tool(tool_cls, mock_service):
    """创建已注入 Mock service 的子工具实例."""
    t = tool_cls()
    inject_identity(t, "test_user", "test_thread", "test-agent")
    acc = HealthDataServiceAccessor("test_user", "test_thread", "test-agent")
    acc._service = mock_service
    object.__setattr__(t, "_health_acc", acc)
    return t


class TestHasValue:
    def test_none_should_return_false(self):
        """None 应返回 False."""
        assert has_value(None) is False

    def test_zero_should_return_false(self):
        """0 应返回 False."""
        assert has_value(0) is False

    def test_positive_value(self):
        """正值应返回 True."""
        assert has_value(10) is True

    def test_float_value(self):
        """浮点值应返回 True."""
        assert has_value(3.14) is True


class TestFirstDayOfMonthNAgo:
    def test_offset_zero_should_return_current_month_first(self):
        """offset=0 应返回当月 1 号."""
        assert first_day_of_month_n_ago(date(2025, 3, 15), 0) == date(2025, 3, 1)

    def test_offset_two_should_return_two_months_ago_first(self):
        """offset=2 应返回两个月前 1 号."""
        assert first_day_of_month_n_ago(date(2025, 3, 15), 2) == date(2025, 1, 1)

    def test_cross_year(self):
        """跨年应正确回退到上一年."""
        assert first_day_of_month_n_ago(date(2025, 1, 10), 1) == date(2024, 12, 1)


class TestUserToday:
    """user_today 按用户时区返回本地日期."""

    def test_returns_local_date_by_user_timezone(self, monkeypatch):
        """UTC 16:00 在 Asia/Shanghai(+8) 应跨日到次日."""
        monkeypatch.setattr(
            "src.tools.internal.health_data_helpers.now_utc",
            lambda: datetime(2026, 7, 17, 16, 0, tzinfo=UTC),
        )
        mock_mgr = Mock()
        mock_mgr.get_user_timezone.return_value = "Asia/Shanghai"
        monkeypatch.setattr(
            "src.tools.internal.health_data_helpers.get_auth_manager",
            lambda: mock_mgr,
        )

        assert user_today("u1") == date(2026, 7, 18)

    def test_different_timezone_yields_different_date(self, monkeypatch):
        """同一 UTC 时刻, 不同时区可能落在不同本地日期."""
        monkeypatch.setattr(
            "src.tools.internal.health_data_helpers.now_utc",
            lambda: datetime(2026, 7, 17, 23, 30, tzinfo=UTC),
        )
        mock_mgr = Mock()
        mock_mgr.get_user_timezone.side_effect = {
            "west": "America/New_York",
            "east": "Asia/Shanghai",
        }.get
        monkeypatch.setattr(
            "src.tools.internal.health_data_helpers.get_auth_manager",
            lambda: mock_mgr,
        )

        assert user_today("west") == date(2026, 7, 17)
        assert user_today("east") == date(2026, 7, 18)


class TestViewHealthSnapshotTool:
    @pytest.mark.asyncio
    async def test_should_return_snapshot(self, mock_service):
        """应返回健康快照并查询数据覆盖."""
        tool = make_tool(ViewHealthSnapshotTool, mock_service)

        result = await tool._arun()

        assert "健康快照" in result
        mock_service.get_data_coverage.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_daily_data_should_still_return_snapshot(self, mock_service):
        """无每日数据时仍应返回快照骨架."""
        tool = make_tool(ViewHealthSnapshotTool, mock_service)

        result = await tool._arun()

        assert "健康快照" in result

    @pytest.mark.asyncio
    async def test_should_format_latest_day_and_activity(self, mock_service):
        """有每日数据 + 运动摘要 + 体检报告时应全部渲染."""
        s = make_summary(
            record_date=date(2025, 1, 30),
            steps=10000,
            body_mass_kg=70.0,
            sleep_duration_hours=7.5,
            weight_7d_avg=70.5,
        )
        mock_service.get_daily_summaries = AsyncMock(return_value=[s])
        mock_service.get_weekly_activity_summary = AsyncMock(
            return_value={
                "status": "success",
                "total_workouts": 3,
                "total_duration_minutes": 120,
            }
        )
        mock_service.get_latest_report = AsyncMock(
            return_value=Mock(
                report_date=date(2025, 1, 1), report_data={"血压": "120/80"}
            )
        )
        tool = make_tool(ViewHealthSnapshotTool, mock_service)

        result = await tool._arun()

        assert "健康快照" in result
        assert "2025-01-30" in result
        assert "近期运动" in result and "3次" in result
        assert "体检报告" in result

    @pytest.mark.asyncio
    async def test_should_report_freshness_when_data_stale(self, mock_service):
        """数据陈旧时应输出新鲜度提示(最新记录距今 > 1 天)."""
        old = make_summary(record_date=date(2020, 1, 1), body_mass_kg=70.0)
        recent = make_summary(record_date=date.today(), steps=5000)
        mock_service.get_daily_summaries = AsyncMock(return_value=[recent, old])
        tool = make_tool(ViewHealthSnapshotTool, mock_service)

        result = await tool._arun()

        # body_mass 最新出现在 2020-01-01, 距今远超 1 天, 应提示
        assert "数据新鲜度" in result
        assert "体重最新" in result


class TestQueryDailyHealthTool:
    @pytest.mark.asyncio
    async def test_target_date_should_format_detail(self, mock_service):
        """指定日期查询有数据时应输出日报详情."""
        s = make_summary(record_date=date(2025, 1, 15), steps=6000)
        mock_service.get_daily_summary = AsyncMock(return_value=s)
        tool = make_tool(QueryDailyHealthTool, mock_service)

        result = await tool._arun(target_date="2025-01-15")

        assert "2025-01-15 健康日报" in result

    @pytest.mark.asyncio
    async def test_target_date_no_data(self, mock_service):
        """指定日期无数据应提示."""
        tool = make_tool(QueryDailyHealthTool, mock_service)

        result = await tool._arun(target_date="2025-01-15")

        assert "无健康数据" in result

    @pytest.mark.asyncio
    async def test_range_should_format_brief_summaries(self, mock_service):
        """日期范围查询有数据时应逐日 brief 格式化."""
        s = make_summary(
            record_date=date(2025, 1, 30),
            steps=8000,
            body_mass_kg=70.0,
        )
        mock_service.get_daily_summaries = AsyncMock(return_value=[s])
        tool = make_tool(QueryDailyHealthTool, mock_service)

        result = await tool._arun(days=7)

        assert "每日明细" in result
        assert "8000步" in result
        assert "70.0kg" in result

    @pytest.mark.asyncio
    async def test_range_no_data(self, mock_service):
        """日期范围无数据应提示."""
        tool = make_tool(QueryDailyHealthTool, mock_service)

        result = await tool._arun(days=7)

        assert "无" in result


class TestQueryMetricTrendTool:
    @pytest.mark.asyncio
    async def test_should_require_metric(self, mock_service):
        """缺少 metric 应由 Pydantic 校验失败并提示."""
        tool = make_tool(QueryMetricTrendTool, mock_service)

        result = await tool._arun()

        assert "Field required" in result
        assert "metric" in result

    @pytest.mark.asyncio
    async def test_with_data_should_format_trend(self, mock_service):
        """有数据时应返回趋势."""
        mock_service.get_metric_history = AsyncMock(
            return_value=[
                {"date": "2025-01-30", "value": 75.0},
                {"date": "2025-01-29", "value": 74.5},
                {"date": "2025-01-28", "value": 74.8},
            ]
        )
        tool = make_tool(QueryMetricTrendTool, mock_service)

        result = await tool._arun(metric="body_mass_kg")

        assert "体重" in result
        assert "75.0" in result

    @pytest.mark.asyncio
    async def test_unsupported_metric_should_return_error(self, mock_service):
        """不在 METRIC_LABELS 的指标应在调用前返回错误, 不查 DB."""
        tool = make_tool(QueryMetricTrendTool, mock_service)

        result = await tool._arun(metric="bad_metric")

        assert "不支持的指标" in result
        mock_service.get_metric_history.assert_not_called()

    @pytest.mark.asyncio
    async def test_weekly_should_format_week_trend(self, mock_service):
        """period=weekly 时应走周维度趋势."""
        w1 = Mock(week_start=date(2025, 1, 27), steps_total=70000)
        w2 = Mock(week_start=date(2025, 1, 20), steps_total=65000)
        mock_service.get_weekly_summaries = AsyncMock(return_value=[w1, w2])
        tool = make_tool(QueryMetricTrendTool, mock_service)

        result = await tool._arun(metric="steps_total", period="weekly")

        assert "周趋势" in result
        assert "70000.0" in result

    @pytest.mark.asyncio
    async def test_weekly_no_metric_data_should_return_message(self, mock_service):
        """周汇总存在但指定指标全 None 时应提示无该指标数据."""
        w = Mock(week_start=date(2025, 1, 27))
        w.steps_total = None
        mock_service.get_weekly_summaries = AsyncMock(return_value=[w])
        tool = make_tool(QueryMetricTrendTool, mock_service)

        result = await tool._arun(metric="steps_total", period="weekly")

        assert "无" in result

    @pytest.mark.asyncio
    async def test_many_points_should_show_recent_and_gaps(self, mock_service):
        """超过5个数据点应输出近期列表, 存在 > 3 天间隔应报告断档."""
        history = [
            {"date": "2025-01-30", "value": 75.0},
            {"date": "2025-01-29", "value": 74.0},
            {"date": "2025-01-28", "value": 73.0},
            {"date": "2025-01-20", "value": 72.0},
            {"date": "2025-01-19", "value": 71.0},
            {"date": "2025-01-18", "value": 70.0},
        ]
        mock_service.get_metric_history = AsyncMock(return_value=history)
        tool = make_tool(QueryMetricTrendTool, mock_service)

        result = await tool._arun(metric="body_mass_kg", days=30)

        assert "近期" in result
        assert "断档" in result


class TestCompareHealthPeriodsTool:
    @pytest.mark.asyncio
    async def test_should_require_metric(self, mock_service):
        """缺少 metric 应由 Pydantic 校验失败并提示."""
        tool = make_tool(CompareHealthPeriodsTool, mock_service)

        result = await tool._arun()

        assert "Field required" in result
        assert "metric" in result

    @pytest.mark.asyncio
    async def test_month_period_should_format(self, mock_service):
        """period_type=month 时应走月环比路径."""
        mock_service.get_metric_comparison = AsyncMock(
            return_value={
                "period1": {
                    "start": "2025-01-01",
                    "end": "2025-01-15",
                    "avg": 8000,
                    "count": 10,
                },
                "period2": {
                    "start": "2024-12-01",
                    "end": "2024-12-31",
                    "avg": 7500,
                    "count": 25,
                },
                "change_pct": 6.7,
                "direction": "up",
            }
        )
        tool = make_tool(CompareHealthPeriodsTool, mock_service)

        result = await tool._arun(metric="steps", period_type="month")

        assert "步数时段对比" in result
        assert "↑" in result

    @pytest.mark.asyncio
    async def test_month_offset_gt_one_uses_correct_months(
        self, mock_service, monkeypatch
    ):
        """月环比 offset>1 时, p1 应为 offset 个月前."""
        from datetime import date as real_date

        monkeypatch.setattr(
            "src.tools.internal.compare_health_periods_tool.user_today",
            lambda user_id: real_date(2025, 3, 15),
        )
        tool = make_tool(CompareHealthPeriodsTool, mock_service)

        await tool._arun(
            metric="steps",
            period_type="month",
            period_offset=2,
        )

        args = mock_service.get_metric_comparison.call_args.args
        p1_start, p1_end, p2_start, p2_end = args[1], args[2], args[3], args[4]
        assert p1_start == real_date(2025, 1, 1)
        assert p1_end == real_date(2025, 1, 31)
        assert p2_start == real_date(2024, 12, 1)
        assert p2_end == real_date(2024, 12, 31)

    @pytest.mark.asyncio
    async def test_both_periods_empty_should_return_message(self, mock_service):
        mock_service.get_metric_comparison = AsyncMock(
            return_value={"period1": {"count": 0}, "period2": {"count": 0}}
        )
        tool = make_tool(CompareHealthPeriodsTool, mock_service)

        result = await tool._arun(metric="steps")

        assert "均无" in result


class TestListWorkoutRecordsTool:
    @pytest.mark.asyncio
    async def test_list_should_format_records(self, mock_service):
        """运动列表应渲染类型/时长/距离/卡路里/心率."""
        r = Mock()
        r.start_time = datetime(2025, 1, 30, 8, 0)
        r.workout_type = "Running"
        r.duration = 30.0
        r.distance = 5.0
        r.calories = 300.0
        r.heart_rate_avg = 140.0
        mock_service.get_workout_history_filtered = AsyncMock(return_value=[r])
        tool = make_tool(ListWorkoutRecordsTool, mock_service)

        result = await tool._arun(mode="list")

        assert "Running" in result
        assert "30min" in result
        assert "5.0km" in result
        assert "300kcal" in result
        assert "心率140" in result

    @pytest.mark.asyncio
    async def test_list_with_type_filter(self, mock_service):
        """指定 workout_type 时表头应包含类型."""
        mock_service.get_workout_history_filtered = AsyncMock(return_value=[])
        tool = make_tool(ListWorkoutRecordsTool, mock_service)

        result = await tool._arun(workout_type="Cycling")

        assert "Cycling" in result

    @pytest.mark.asyncio
    async def test_stats_should_aggregate(self, mock_service):
        """stats 模式有数据时应输出总计/频率/类型分布."""
        mock_service.get_workout_stats = AsyncMock(
            return_value={
                "status": "success",
                "total_count": 5,
                "total_duration_minutes": 150.0,
                "freq_per_week": 3.5,
                "type_distribution": {"Running": {"count": 3, "duration": 90.0}},
            }
        )
        tool = make_tool(ListWorkoutRecordsTool, mock_service)

        result = await tool._arun(mode="stats")

        assert "总计" in result and "5次" in result
        assert "频率" in result and "3.5" in result
        assert "类型分布" in result
        assert "Running" in result


class TestListMealRecordsTool:
    @pytest.mark.asyncio
    async def test_target_date_should_format_meal_detail(self, mock_service):
        """单日查询有数据时应输出各餐详情."""
        mock_service.get_nutrition_summary = AsyncMock(
            return_value={
                "status": "success",
                "calories": 1500.0,
                "protein": 80.0,
                "carbs": 200.0,
                "fat": 50.0,
                "meal_count": 1,
                "meals": [
                    {
                        "meal_type": "lunch",
                        "meal_time": "12:00",
                        "items": [{"name": "米饭", "quantity": 1, "calories": 200.0}],
                    }
                ],
            }
        )
        tool = make_tool(ListMealRecordsTool, mock_service)

        result = await tool._arun(target_date="2025-01-30")

        assert "2025-01-30 饮食记录" in result
        assert "各餐详情" in result
        assert "米饭" in result
        assert "[lunch] 12:00" in result

    @pytest.mark.asyncio
    async def test_range_should_format_daily_nutrition(self, mock_service, monkeypatch):
        """多日查询应一次批量取数并逐日输出营养摄入."""
        fixed = date(2025, 1, 30)
        monkeypatch.setattr(
            "src.tools.internal.list_meal_records_tool.user_today",
            lambda user_id: fixed,
        )
        mock_service.get_nutrition_range = AsyncMock(
            return_value={
                "2025-01-30": {
                    "status": "success",
                    "calories": 1500.0,
                    "protein": 80.0,
                    "carbs": 200.0,
                    "fat": 50.0,
                    "meal_count": 3,
                },
                "2025-01-29": {
                    "status": "success",
                    "calories": 1200.0,
                    "protein": 60.0,
                    "carbs": 160.0,
                    "fat": 40.0,
                    "meal_count": 2,
                },
            }
        )
        tool = make_tool(ListMealRecordsTool, mock_service)

        result = await tool._arun(days=3)

        assert "饮食记录" in result
        assert "1500kcal" in result
        assert "1200kcal" in result
        # 多日查询应只调用一次批量接口, 而非逐日 N 次
        assert mock_service.get_nutrition_range.await_count == 1


class TestViewMedicalReportTool:
    @pytest.mark.asyncio
    async def test_should_format_report_detail_and_history(self, mock_service):
        """有报告数据时应渲染类型/数据项/历史."""
        mock_service.get_report_detail = AsyncMock(
            return_value={
                "status": "success",
                "latest": {
                    "report_date": "2025-01-01",
                    "report_type": "routine",
                    "data": {"血压": "120/80", "心率": 72},
                },
                "history": {"total_reports": 3},
            }
        )
        tool = make_tool(ViewMedicalReportTool, mock_service)

        result = await tool._arun()

        assert "体检报告" in result
        assert "routine" in result
        assert "120/80" in result
        assert "历史报告" in result

    @pytest.mark.asyncio
    async def test_no_data(self, mock_service):
        """无报告应提示."""
        tool = make_tool(ViewMedicalReportTool, mock_service)

        result = await tool._arun()

        assert "暂无" in result


class TestListShoppingItemsTool:
    @pytest.mark.asyncio
    async def test_should_format_shopping_items(self, mock_service):
        """购物清单应渲染日期/名称/数量/备注."""
        item = Mock()
        item.purchase_date = date(2025, 1, 30)
        item.name = "牛奶"
        item.quantity = 2
        item.notes = "低脂"
        mock_service.get_shopping_list = AsyncMock(return_value=[item])
        tool = make_tool(ListShoppingItemsTool, mock_service)

        result = await tool._arun(days=30)

        assert "2025-01-30" in result
        assert "牛奶" in result
        assert "x2" in result
        assert "低脂" in result


class TestFormatBriefFields:
    """format_brief_fields 各分区分支."""

    @pytest.mark.asyncio
    async def test_all_categories_should_render(self, mock_service):
        """活动/体征/睡眠/均值四类全有数据时应全部渲染."""
        from datetime import time

        s = make_summary(
            record_date=date(2025, 1, 30),
            steps=10000,
            active_energy_kcal=500.0,
            apple_exercise_minutes=40.0,
            distance_km=6.0,
            stand_hours=11,
            body_mass_kg=70.0,
            body_fat_pct=18.0,
            muscle_mass_kg=30.0,
            resting_hr_bpm=60,
            hrv_ms=50,
            vo2_max=45.0,
            blood_oxygen_pct=98,
            sleep_duration_hours=7.5,
            sleep_efficiency=90.0,
            deep_sleep_minutes=80,
            bed_time=time(23, 0),
            wake_time=time(7, 0),
            weight_7d_avg=70.5,
            steps_7d_avg=9000,
        )
        mock_service.get_daily_summaries = AsyncMock(return_value=[s])
        tool = make_tool(ViewHealthSnapshotTool, mock_service)

        result = await tool._arun()

        assert "活动:" in result
        assert "体征:" in result
        assert "睡眠:" in result
        assert "均值:" in result
        assert "起床" in result
