"""健康数据管理工具 - 数据路径(成功路径)单元测试.

现有测试(test_health_data_manager_tool.py)主要覆盖各 action 的"无数据"分支,
本文件补充"有数据"的成功路径与格式化逻辑:
- get_overview 快照(最新日 + 运动摘要 + 体检报告 + 数据新鲜度)
- get_daily 范围明细(brief 格式化)
- get_trend 周维度 / 多点近期 / 断档检测
- get_comparison 月环比 / 无数据 / 缺均值
- get_workout 列表与统计的成功路径
- get_meals 多日与单日营养详情
- get_report 报告详情与历史
- get_shopping 购物清单
- _format_brief_fields 全分支(活动/体征/睡眠/均值)

Mock边界: Mock _get_service() 返回 Mock HealthDataService, 保留真实格式化逻辑.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from src.tools.internal.health_data_manager_tool import HealthDataManagerTool

# 单日汇总模型用到的全部字段
_SUMMARY_FIELDS = [
    "record_date", "steps", "active_energy_kcal", "basal_energy_kcal", "distance_km",
    "apple_exercise_minutes", "stand_hours", "body_mass_kg", "body_fat_pct",
    "muscle_mass_kg", "resting_hr_bpm", "hrv_ms", "vo2_max", "avg_hr_bpm",
    "min_hr_bpm", "max_hr_bpm", "blood_oxygen_pct", "wrist_temperature",
    "respiratory_rate", "sleep_duration_hours", "sleep_efficiency", "asleep_minutes",
    "deep_sleep_minutes", "rem_sleep_minutes", "core_sleep_minutes", "awake_minutes",
    "flights_climbed", "sunlight_minutes", "weight_7d_avg", "steps_7d_avg",
    "resting_hr_7d_avg", "hrv_7d_avg", "sleep_7d_avg", "sleep_efficiency_7d_avg",
    "exercise_7d_total", "bed_time", "wake_time",
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
    return service


@pytest.fixture
def tool(mock_service):
    """创建已注入 Mock service 的工具实例."""
    t = HealthDataManagerTool(
        user_id="test_user", thread_id="test_thread", agent_id="test-agent"
    )
    object.__setattr__(t, "_health_service", mock_service)
    return t


class TestGetOverviewDataPath:
    @pytest.mark.asyncio
    async def test_should_format_latest_day_and_activity(self, tool, mock_service):
        """有每日数据 + 运动摘要 + 体检报告时应全部渲染."""
        s = make_summary(
            record_date=date(2025, 1, 30), steps=10000, body_mass_kg=70.0,
            sleep_duration_hours=7.5, weight_7d_avg=70.5,
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
            return_value=Mock(report_date=date(2025, 1, 1), report_data={"血压": "120/80"})
        )

        result = await tool._arun(action="get_overview")

        assert "健康快照" in result
        assert "2025-01-30" in result
        assert "近期运动" in result and "3次" in result
        assert "体检报告" in result

    @pytest.mark.asyncio
    async def test_should_report_freshness_when_data_stale(self, tool, mock_service):
        """数据陈旧时应输出新鲜度提示(需要历史日期距今 > 1 天)."""
        old = make_summary(record_date=date(2020, 1, 1), body_mass_kg=70.0)
        recent = make_summary(record_date=date.today(), steps=5000)
        mock_service.get_daily_summaries = AsyncMock(return_value=[recent, old])

        result = await tool._arun(action="get_overview")

        # body_mass 最新出现在 2020-01-01, 距今远超 1 天, 应提示
        assert "数据新鲜度" in result
        assert "体重最新" in result


class TestGetDailyDataPath:
    @pytest.mark.asyncio
    async def test_range_should_format_brief_summaries(self, tool, mock_service):
        """日期范围查询有数据时应逐日 brief 格式化."""
        s = make_summary(
            record_date=date(2025, 1, 30), steps=8000, body_mass_kg=70.0,
        )
        mock_service.get_daily_summaries = AsyncMock(return_value=[s])

        result = await tool._arun(action="get_daily", days=7)

        assert "每日明细" in result
        assert "8000步" in result
        assert "70.0kg" in result

    @pytest.mark.asyncio
    async def test_target_date_should_format_detail(self, tool, mock_service):
        """指定日期查询有数据时应输出日报详情."""
        s = make_summary(record_date=date(2025, 1, 15), steps=6000)
        mock_service.get_daily_summary = AsyncMock(return_value=s)

        result = await tool._arun(action="get_daily", target_date="2025-01-15")

        assert "2025-01-15 健康日报" in result


class TestGetTrendDataPath:
    @pytest.mark.asyncio
    async def test_weekly_should_format_week_trend(self, tool, mock_service):
        """period=weekly 时应走周维度趋势."""
        w1 = Mock(week_start=date(2025, 1, 27), steps_total=70000)
        w2 = Mock(week_start=date(2025, 1, 20), steps_total=65000)
        mock_service.get_weekly_summaries = AsyncMock(return_value=[w1, w2])

        result = await tool._arun(
            action="get_trend", metric="steps_total", period="weekly"
        )

        assert "周趋势" in result
        assert "70000.0" in result

    @pytest.mark.asyncio
    async def test_many_points_should_show_recent_and_gaps(self, tool, mock_service):
        """超过5个数据点应输出近期列表, 存在 > 3 天间隔应报告断档."""
        history = [
            {"date": "2025-01-30", "value": 75.0},
            {"date": "2025-01-29", "value": 74.0},
            {"date": "2025-01-28", "value": 73.0},
            {"date": "2025-01-20", "value": 72.0},  # 28→20 间隔 8 天
            {"date": "2025-01-19", "value": 71.0},
            {"date": "2025-01-18", "value": 70.0},
        ]
        mock_service.get_metric_history = AsyncMock(return_value=history)

        result = await tool._arun(action="get_trend", metric="body_mass_kg", days=30)

        assert "近期" in result
        assert "断档" in result

    @pytest.mark.asyncio
    async def test_unsupported_metric_should_return_error(self, tool, mock_service):
        """service.get_metric_history 抛 AttributeError 时应返回不支持指标."""
        mock_service.get_metric_history = AsyncMock(side_effect=AttributeError())

        result = await tool._arun(action="get_trend", metric="bad_metric")

        assert "不支持的指标" in result

    @pytest.mark.asyncio
    async def test_weekly_no_metric_data_should_return_message(self, tool, mock_service):
        """周汇总存在但指定指标全 None 时应提示无该指标数据."""
        w = Mock(week_start=date(2025, 1, 27))
        w.steps_total = None  # getattr 返回 None
        mock_service.get_weekly_summaries = AsyncMock(return_value=[w])

        result = await tool._arun(
            action="get_trend", metric="steps_total", period="weekly"
        )

        assert "无" in result


class TestGetComparisonDataPath:
    @pytest.mark.asyncio
    async def test_month_period_should_format(self, tool, mock_service):
        """period_type=month 时应走月环比路径."""
        mock_service.get_metric_comparison = AsyncMock(
            return_value={
                "period1": {"start": "2025-01-01", "end": "2025-01-15", "avg": 8000, "count": 10},
                "period2": {"start": "2024-12-01", "end": "2024-12-31", "avg": 7500, "count": 25},
                "change_pct": 6.7,
                "direction": "up",
            }
        )

        result = await tool._arun(
            action="get_comparison", metric="steps", period_type="month"
        )

        assert "步数时段对比" in result
        assert "↑" in result

    @pytest.mark.asyncio
    async def test_both_periods_empty_should_return_message(self, tool, mock_service):
        mock_service.get_metric_comparison = AsyncMock(
            return_value={"period1": {"count": 0}, "period2": {"count": 0}}
        )

        result = await tool._arun(action="get_comparison", metric="steps")

        assert "均无" in result

    @pytest.mark.asyncio
    async def test_period_without_avg_should_show_no_data(self, tool, mock_service):
        mock_service.get_metric_comparison = AsyncMock(
            return_value={
                "period1": {"start": "a", "end": "b", "avg": None, "count": 0},
                "period2": {"start": "c", "end": "d", "avg": 100.0, "count": 5},
            }
        )

        result = await tool._arun(action="get_comparison", metric="steps")

        assert "无数据" in result


class TestGetWorkoutDataPath:
    @pytest.mark.asyncio
    async def test_list_should_format_records(self, tool, mock_service):
        """运动列表应渲染类型/时长/距离/卡路里/心率."""
        r = Mock()
        r.start_time = datetime(2025, 1, 30, 8, 0)
        r.workout_type = "Running"
        r.duration = 30.0
        r.distance = 5.0
        r.calories = 300.0
        r.heart_rate_avg = 140.0
        mock_service.get_workout_history_filtered = AsyncMock(return_value=[r])

        result = await tool._arun(action="get_workout", mode="list")

        assert "Running" in result
        assert "30min" in result
        assert "5.0km" in result
        assert "300kcal" in result
        assert "心率140" in result

    @pytest.mark.asyncio
    async def test_list_with_type_filter(self, tool, mock_service):
        """指定 workout_type 时表头应包含类型."""
        mock_service.get_workout_history_filtered = AsyncMock(return_value=[])

        result = await tool._arun(action="get_workout", workout_type="Cycling")

        assert "Cycling" in result

    @pytest.mark.asyncio
    async def test_stats_should_aggregate(self, tool, mock_service):
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

        result = await tool._arun(action="get_workout", mode="stats")

        assert "总计" in result and "5次" in result
        assert "频率" in result and "3.5" in result
        assert "类型分布" in result
        assert "Running" in result


class TestGetMealsDataPath:
    @pytest.mark.asyncio
    async def test_range_should_format_daily_nutrition(self, tool, mock_service):
        """多日查询有数据时应逐日输出营养摄入."""
        mock_service.get_nutrition_summary = AsyncMock(
            return_value={
                "status": "success",
                "calories": 1500.0,
                "protein": 80.0,
                "carbs": 200.0,
                "fat": 50.0,
                "meal_count": 3,
            }
        )

        result = await tool._arun(action="get_meals", days=3)

        assert "饮食记录" in result
        assert "1500kcal" in result

    @pytest.mark.asyncio
    async def test_target_date_should_format_meal_detail(self, tool, mock_service):
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

        result = await tool._arun(action="get_meals", target_date="2025-01-30")

        assert "2025-01-30 饮食记录" in result
        assert "各餐详情" in result
        assert "米饭" in result
        assert "[lunch] 12:00" in result


class TestGetReportDataPath:
    @pytest.mark.asyncio
    async def test_should_format_report_detail_and_history(self, tool, mock_service):
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

        result = await tool._arun(action="get_report")

        assert "体检报告" in result
        assert "routine" in result
        assert "120/80" in result
        assert "历史报告" in result


class TestGetShoppingDataPath:
    @pytest.mark.asyncio
    async def test_should_format_shopping_items(self, tool, mock_service):
        """购物清单应渲染日期/名称/数量/备注."""
        item = Mock()
        item.purchase_date = date(2025, 1, 30)
        item.name = "牛奶"
        item.quantity = 2
        item.notes = "低脂"
        mock_service.get_shopping_list = AsyncMock(return_value=[item])

        result = await tool._arun(action="get_shopping", days=30)

        assert "2025-01-30" in result
        assert "牛奶" in result
        assert "x2" in result
        assert "低脂" in result


class TestFormatBriefFields:
    """_format_brief_fields 各分区分支."""

    @pytest.mark.asyncio
    async def test_all_categories_should_render(self, tool, mock_service):
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

        result = await tool._arun(action="get_overview")

        assert "活动:" in result
        assert "体征:" in result
        assert "睡眠:" in result
        assert "均值:" in result
        assert "起床" in result
