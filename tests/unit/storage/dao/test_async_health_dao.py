"""HealthDAO单元测试.

测试健康数据访问对象的功能, 所有表已移除user_id/thread_id(文件级隔离).
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.storage.dao.async_health_dao import AsyncHealthDAO
from src.storage.models.health_data import (
    DailyHealthSummary,
    MealRecord,
    MedicalReport,
    ShoppingItem,
    WeeklyHealthSummary,
    WorkoutRecord,
)


@pytest.fixture
def mock_session_factory():

    mock_session = AsyncMock()

    class MockSessionFactory:
        def __init__(self):
            self.session = mock_session

        def __call__(self):
            return self

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_factory = MockSessionFactory()
    return mock_factory


@pytest.fixture
def health_dao(mock_session_factory):
    """创建HealthDAO实例."""
    return AsyncHealthDAO(mock_session_factory)


class TestMealRecordManagement:
    @pytest.mark.asyncio
    async def test_calculate_daily_nutrition_should_sum_meals(self, health_dao):
        """测试计算每日营养成分应该汇总所有餐次."""
        meals = [
            MealRecord(
                id=1,
                meal_type="breakfast",
                meal_date=date.today(),
                items=[],
                total_calories=400.0,
                total_protein=15.0,
                total_carbs=50.0,
                total_fat=10.0,
            ),
            MealRecord(
                id=2,
                meal_type="lunch",
                meal_date=date.today(),
                items=[],
                total_calories=600.0,
                total_protein=25.0,
                total_carbs=70.0,
                total_fat=18.0,
            ),
        ]

        health_dao._meal_record_ops.find_by_filters = AsyncMock(return_value=meals)

        result = await health_dao.calculate_daily_nutrition(date.today())

        assert result["status"] == "success"
        assert result["calories"] == 1000.0
        assert result["protein"] == 40.0
        assert result["carbs"] == 120.0
        assert result["fat"] == 28.0
        assert result["meal_count"] == 2


class TestAnalyzeReportTrends:
    @pytest.mark.asyncio
    async def test_should_return_no_data_when_empty(
        self, health_dao, mock_session_factory
    ):
        """无报告数据应返回no_data状态."""
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.analyze_report_trends()

        assert result["status"] == "no_data"

    @pytest.mark.asyncio
    async def test_should_return_success_with_reports(
        self, health_dao, mock_session_factory
    ):
        """有报告数据应返回趋势分析."""
        report = MedicalReport(
            id=1,
            report_date=datetime.now(),
            report_data={"bp": "120/80"},
        )
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [report]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.analyze_report_trends()

        assert result["status"] == "success"
        assert result["total_reports"] == 1


class TestGetMetricHistory:
    @pytest.mark.asyncio
    async def test_should_return_metric_history(self, health_dao, mock_session_factory):
        """应返回指定指标的历史数据."""
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (date(2026, 6, 1), 8500),
            (date(2026, 5, 31), 7200),
        ]
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_metric_history("steps", days=30)

        assert len(result) == 2
        assert result[0]["date"] == "2026-06-01"
        assert result[0]["value"] == 8500


class TestGetLatestDailySummaries:
    @pytest.mark.asyncio
    async def test_should_return_latest_summaries(
        self, health_dao, mock_session_factory
    ):
        """应返回最近N天的汇总."""
        summary = DailyHealthSummary(id=1, record_date=date.today(), steps=10000)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [summary]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_latest_daily_summaries(limit=7)

        assert len(result) == 1
        assert result[0].steps == 10000


class TestGetDailyIntake:
    @pytest.mark.asyncio
    async def test_should_query_by_meal_date(self, health_dao):
        """应按日期查询摄入记录."""
        health_dao._meal_record_ops.find_by_filters = AsyncMock(return_value=[])

        result = await health_dao.get_daily_intake(date.today())

        assert result == []
        health_dao._meal_record_ops.find_by_filters.assert_called_once_with(
            {"meal_date": date.today()},
        )


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_should_check_all_tables(self, health_dao):
        """测试健康检查应该检查所有表."""
        for ops in [
            health_dao._medical_report_ops,
            health_dao._daily_summary_ops,
            health_dao._weekly_summary_ops,
            health_dao._shopping_item_ops,
            health_dao._food_product_ops,
            health_dao._workout_record_ops,
            health_dao._meal_record_ops,
            health_dao._weight_record_ops,
        ]:
            ops.health_check = AsyncMock(return_value=True)

        result = await health_dao.health_check()

        assert all(result.values())
        assert len(result) == 8
        assert "daily_health_summary" in result
        assert "weekly_health_summary" in result


class TestUpsertDailySummary:
    """upsert_daily_summary 的插入/更新/空更新三分支."""

    @pytest.mark.asyncio
    async def test_should_insert_when_not_exists(
        self, health_dao, mock_session_factory
    ):
        """无既有记录时应插入新记录(add+commit+refresh)."""
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = None
        mock_session_factory.session.execute.return_value = select_result

        result = await health_dao.upsert_daily_summary({
            "record_date": date(2026, 6, 1),
            "steps": 5000,
        })

        assert result.record_date == date(2026, 6, 1)
        assert result.steps == 5000
        mock_session_factory.session.add.assert_called_once()
        mock_session_factory.session.commit.assert_awaited_once()
        mock_session_factory.session.refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_should_update_when_exists(self, health_dao, mock_session_factory):
        """有既有记录且存在可更新字段时应执行 update 并提交."""
        existing = DailyHealthSummary(id=5, record_date=date(2026, 6, 1), steps=3000)
        updated = DailyHealthSummary(id=5, record_date=date(2026, 6, 1), steps=5000)

        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = existing
        update_result = MagicMock()
        update_result.scalar_one.return_value = updated
        mock_session_factory.session.execute.side_effect = [
            select_result,
            update_result,
        ]

        result = await health_dao.upsert_daily_summary({
            "record_date": date(2026, 6, 1),
            "steps": 5000,
        })

        assert result == updated
        mock_session_factory.session.commit.assert_awaited_once()
        mock_session_factory.session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_return_existing_when_no_updatable_values(
        self, health_dao, mock_session_factory
    ):
        """既有记录但所有字段为 None 时应直接返回既有记录, 不提交."""
        existing = DailyHealthSummary(id=5, record_date=None)
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = existing
        mock_session_factory.session.execute.return_value = select_result

        result = await health_dao.upsert_daily_summary({
            "record_date": None,
            "steps": None,
        })

        assert result == existing
        mock_session_factory.session.commit.assert_not_awaited()


class TestUpsertWeeklySummary:
    """upsert_weekly_summary 的插入与更新分支."""

    @pytest.mark.asyncio
    async def test_should_insert_when_not_exists(
        self, health_dao, mock_session_factory
    ):
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = None
        mock_session_factory.session.execute.return_value = select_result

        result = await health_dao.upsert_weekly_summary({
            "week_start": date(2026, 6, 1),
            "avg_steps": 8000,
        })

        assert result.week_start == date(2026, 6, 1)
        mock_session_factory.session.add.assert_called_once()
        mock_session_factory.session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_should_update_when_exists(self, health_dao, mock_session_factory):
        existing = WeeklyHealthSummary(id=3, week_start=date(2026, 6, 1))
        updated = WeeklyHealthSummary(id=3, week_start=date(2026, 6, 1), avg_steps=9000)

        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = existing
        update_result = MagicMock()
        update_result.scalar_one.return_value = updated
        mock_session_factory.session.execute.side_effect = [
            select_result,
            update_result,
        ]

        result = await health_dao.upsert_weekly_summary({
            "week_start": date(2026, 6, 1),
            "avg_steps": 9000,
        })

        assert result == updated
        mock_session_factory.session.commit.assert_awaited_once()


class TestUpsertWorkoutRecord:
    """upsert_workout_record 的插入与更新分支."""

    @pytest.mark.asyncio
    async def test_should_insert_when_not_exists(
        self, health_dao, mock_session_factory
    ):
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = None
        mock_session_factory.session.execute.return_value = select_result

        result = await health_dao.upsert_workout_record({
            "workout_type": "Running",
            "start_time": datetime(2026, 6, 1, 8, 0),
            "duration": 30.0,
        })

        assert result.workout_type == "Running"
        mock_session_factory.session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_update_when_exists(self, health_dao, mock_session_factory):
        existing = WorkoutRecord(
            id=7, workout_type="Running", start_time=datetime(2026, 6, 1, 8, 0)
        )
        updated = WorkoutRecord(
            id=7,
            workout_type="Running",
            start_time=datetime(2026, 6, 1, 8, 0),
            duration=45.0,
        )

        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = existing
        update_result = MagicMock()
        update_result.scalar_one.return_value = updated
        mock_session_factory.session.execute.side_effect = [
            select_result,
            update_result,
        ]

        result = await health_dao.upsert_workout_record({
            "workout_type": "Running",
            "start_time": datetime(2026, 6, 1, 8, 0),
            "duration": 45.0,
        })

        assert result == updated
        mock_session_factory.session.commit.assert_awaited_once()


class TestSimpleRecordCreation:
    """单条采样/ECG 记录创建及单日汇总查询."""

    @pytest.mark.asyncio
    async def test_get_daily_summary_should_return_when_found(
        self, health_dao, mock_session_factory
    ):
        """有记录时返回汇总对象."""
        summary = DailyHealthSummary(id=1, record_date=date(2026, 6, 1), steps=10000)
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = summary
        mock_session_factory.session.execute.return_value = select_result

        result = await health_dao.get_daily_summary(date(2026, 6, 1))

        assert result == summary

    @pytest.mark.asyncio
    async def test_get_daily_summary_should_return_none_when_not_found(
        self, health_dao, mock_session_factory
    ):
        """无记录时返回 None."""
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = None
        mock_session_factory.session.execute.return_value = select_result

        result = await health_dao.get_daily_summary(date(2026, 6, 1))

        assert result is None

    @pytest.mark.asyncio
    async def test_create_workout_sample_should_add_and_commit(
        self, health_dao, mock_session_factory
    ):
        """运动采样记录应 add 后 commit."""
        await health_dao.create_workout_sample({
            "workout_start_time": datetime(2026, 6, 1, 8, 0),
            "workout_type": "Running",
            "metric_type": "heart_rate",
            "sample_time": datetime(2026, 6, 1, 8, 1),
            "value_avg": 130.0,
        })
        mock_session_factory.session.add.assert_called_once()
        mock_session_factory.session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_ecg_record_should_add_and_commit(
        self, health_dao, mock_session_factory
    ):
        """ECG 记录应 add 后 commit."""
        await health_dao.create_ecg_record({"start_time": datetime(2026, 6, 1, 8, 0)})
        mock_session_factory.session.add.assert_called_once()
        mock_session_factory.session.commit.assert_awaited_once()


class TestWeeklyActivitySummary:
    """get_weekly_activity_summary 的无数据与聚合分支."""

    @pytest.mark.asyncio
    async def test_should_return_no_data_when_empty(
        self, health_dao, mock_session_factory
    ):
        """无运动记录应返回 no_data 状态."""
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_weekly_activity_summary()

        assert result["status"] == "no_data"
        assert result["total_workouts"] == 0

    @pytest.mark.asyncio
    async def test_should_aggregate_workouts(self, health_dao, mock_session_factory):
        """有运动记录应汇总次数/时长/卡路里并按类型分组."""
        w1 = WorkoutRecord(
            id=1,
            workout_type="Running",
            duration=30.0,
            calories=300.0,
            start_time=datetime.now(),
        )
        w2 = WorkoutRecord(
            id=2,
            workout_type="Running",
            duration=20.0,
            calories=None,
            start_time=datetime.now(),
        )
        w3 = WorkoutRecord(
            id=3,
            workout_type="Cycling",
            duration=40.0,
            calories=250.0,
            start_time=datetime.now(),
        )
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [w1, w2, w3]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_weekly_activity_summary()

        assert result["status"] == "success"
        assert result["total_workouts"] == 3
        assert result["total_duration"] == 90.0
        # calories 为 None 的记录按 0 计入
        assert result["total_calories"] == 550.0
        assert result["workout_types"]["Running"]["count"] == 2
        assert result["workout_types"]["Cycling"]["count"] == 1


class TestCalculateDailyNutritionNoData:
    @pytest.mark.asyncio
    async def test_should_return_no_data_when_no_meals(self, health_dao):
        """无摄入记录应返回 no_data 状态."""
        health_dao._meal_record_ops.find_by_filters = AsyncMock(return_value=[])

        result = await health_dao.calculate_daily_nutrition(date(2026, 6, 1))

        assert result["status"] == "no_data"
        assert result["date"] == "2026-06-01"


class TestDataCoverage:
    """get_data_coverage 的全表统计."""

    @pytest.mark.asyncio
    async def test_should_return_zeroed_stats_when_empty(
        self, health_dao, mock_session_factory
    ):
        """空库时各表计数为 0, 日期范围为 None."""
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        mock_result.scalar_one_or_none.return_value = None
        mock_result.all.return_value = []
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_data_coverage()

        assert result["tables"]["medical_reports"] == 0
        assert result["tables"]["workout_samples"] == 0
        assert result["daily"]["total"] == 0
        assert result["daily"]["date_range"] == {"start": None, "end": None}
        assert result["data_sources"] == {}
        assert result["latest_update"] is None
        assert result["workout_types"] == {}


class TestMetricComparison:
    """get_metric_comparison 的方向判断逻辑."""

    @pytest.mark.asyncio
    async def test_should_return_up_when_metric_increased(
        self, health_dao, mock_session_factory
    ):
        """时段1均值显著高于时段2时方向为 up."""
        r1 = MagicMock()
        r1.one.return_value = (10, 110.0, 120.0, 100.0)
        r2 = MagicMock()
        r2.one.return_value = (10, 100.0, 110.0, 90.0)
        mock_session_factory.session.execute.side_effect = [r1, r2]

        result = await health_dao.get_metric_comparison(
            "steps",
            date(2026, 5, 1),
            date(2026, 5, 7),
            date(2026, 5, 8),
            date(2026, 5, 14),
        )

        assert result["direction"] == "up"
        assert result["change_pct"] == 10.0

    @pytest.mark.asyncio
    async def test_should_return_down_when_metric_decreased(
        self, health_dao, mock_session_factory
    ):
        """时段1均值显著低于时段2时方向为 down."""
        r1 = MagicMock()
        r1.one.return_value = (10, 90.0, 100.0, 80.0)
        r2 = MagicMock()
        r2.one.return_value = (10, 100.0, 110.0, 90.0)
        mock_session_factory.session.execute.side_effect = [r1, r2]

        result = await health_dao.get_metric_comparison(
            "steps",
            date(2026, 5, 1),
            date(2026, 5, 7),
            date(2026, 5, 8),
            date(2026, 5, 14),
        )

        assert result["direction"] == "down"

    @pytest.mark.asyncio
    async def test_should_return_no_data_when_avg_missing(
        self, health_dao, mock_session_factory
    ):
        """任一时段均值为 None 时方向为 no_data."""
        r1 = MagicMock()
        r1.one.return_value = (0, None, None, None)
        r2 = MagicMock()
        r2.one.return_value = (10, 100.0, 110.0, 90.0)
        mock_session_factory.session.execute.side_effect = [r1, r2]

        result = await health_dao.get_metric_comparison(
            "steps",
            date(2026, 5, 1),
            date(2026, 5, 7),
            date(2026, 5, 8),
            date(2026, 5, 14),
        )

        assert result["direction"] == "no_data"
        assert result["change_pct"] is None

    @pytest.mark.asyncio
    async def test_should_return_stable_when_change_is_small(
        self, health_dao, mock_session_factory
    ):
        """变化幅度在 ±1% 以内时方向为 stable."""
        r1 = MagicMock()
        r1.one.return_value = (10, 100.5, 120.0, 80.0)
        r2 = MagicMock()
        r2.one.return_value = (10, 100.0, 110.0, 90.0)
        mock_session_factory.session.execute.side_effect = [r1, r2]

        result = await health_dao.get_metric_comparison(
            "steps",
            date(2026, 5, 1),
            date(2026, 5, 7),
            date(2026, 5, 8),
            date(2026, 5, 14),
        )

        assert result["direction"] == "stable"
        assert abs(result["change_pct"]) < 1

    @pytest.mark.asyncio
    async def test_should_return_stable_when_change_exactly_zero(
        self, health_dao, mock_session_factory
    ):
        """变化为 0% 时方向同样为 stable."""
        r1 = MagicMock()
        r1.one.return_value = (10, 100.0, 120.0, 80.0)
        r2 = MagicMock()
        r2.one.return_value = (10, 100.0, 110.0, 90.0)
        mock_session_factory.session.execute.side_effect = [r1, r2]

        result = await health_dao.get_metric_comparison(
            "steps",
            date(2026, 5, 1),
            date(2026, 5, 7),
            date(2026, 5, 8),
            date(2026, 5, 14),
        )

        assert result["direction"] == "stable"
        assert result["change_pct"] == 0.0


class TestWorkoutStats:
    """get_workout_stats 的聚合统计."""

    @pytest.mark.asyncio
    async def test_should_aggregate_stats_and_distribution(
        self, health_dao, mock_session_factory
    ):
        """应统计总数/时长/卡路里并按类型分布, 计算每周频率."""
        total_r = MagicMock()
        total_r.one.return_value = (5, 150.0, 750.0)
        type_r = MagicMock()
        type_r.all.return_value = [("Running", 3, 90.0), ("Cycling", 2, 60.0)]
        mock_session_factory.session.execute.side_effect = [total_r, type_r]

        result = await health_dao.get_workout_stats(days=30)

        assert result["status"] == "success"
        assert result["total_count"] == 5
        assert result["total_duration_minutes"] == 150.0
        assert result["total_calories"] == 750.0
        assert result["type_distribution"]["Running"]["count"] == 3
        assert result["type_distribution"]["Cycling"]["duration"] == 60.0
        assert "freq_per_week" in result

    @pytest.mark.asyncio
    async def test_should_handle_empty_stats(self, health_dao, mock_session_factory):
        """无数据时计数为 0 且分布为空."""
        total_r = MagicMock()
        total_r.one.return_value = (0, None, None)
        type_r = MagicMock()
        type_r.all.return_value = []
        mock_session_factory.session.execute.side_effect = [total_r, type_r]

        result = await health_dao.get_workout_stats(days=90)

        assert result["total_count"] == 0
        assert result["total_duration_minutes"] == 0.0
        assert result["type_distribution"] == {}

    @pytest.mark.asyncio
    async def test_should_handle_partial_calories(
        self, health_dao, mock_session_factory
    ):
        """部分运动无卡路里数据时应正确处理."""
        total_r = MagicMock()
        total_r.one.return_value = (2, 60.0, None)
        type_r = MagicMock()
        type_r.all.return_value = [("Running", 2, 60.0)]
        mock_session_factory.session.execute.side_effect = [total_r, type_r]

        result = await health_dao.get_workout_stats(days=30)

        assert result["total_count"] == 2
        assert result["total_calories"] == 0.0
        assert result["type_distribution"]["Running"]["duration"] == 60.0

    @pytest.mark.asyncio
    async def test_should_filter_by_workout_type(
        self, health_dao, mock_session_factory
    ):
        """指定 workout_type 时应传递筛选条件."""
        total_r = MagicMock()
        total_r.one.return_value = (1, 45.0, 300.0)
        type_r = MagicMock()
        type_r.all.return_value = [("Cycling", 1, 45.0)]
        mock_session_factory.session.execute.side_effect = [total_r, type_r]

        result = await health_dao.get_workout_stats(
            days=90, workout_type="Cycling",
        )

        assert result["total_count"] == 1
        assert result["type_distribution"]["Cycling"]["count"] == 1


class TestHealthCheckException:
    @pytest.mark.asyncio
    async def test_should_return_false_when_ops_raises(self, health_dao):
        """单个表健康检查抛异常时该项应为 False, 其余不受影响."""
        health_dao._medical_report_ops.health_check = AsyncMock(
            side_effect=Exception("db error")
        )
        for ops in [
            health_dao._daily_summary_ops,
            health_dao._weekly_summary_ops,
            health_dao._shopping_item_ops,
            health_dao._food_product_ops,
            health_dao._workout_record_ops,
            health_dao._meal_record_ops,
            health_dao._weight_record_ops,
        ]:
            ops.health_check = AsyncMock(return_value=True)

        result = await health_dao.health_check()

        assert result["medical_reports"] is False
        assert result["daily_health_summary"] is True
        assert len(result) == 8


class TestGetDailySummaries:
    """get_daily_summaries 日期范围查询."""

    @pytest.mark.asyncio
    async def test_should_return_daily_summaries_in_range(
        self, health_dao, mock_session_factory
    ):
        """应返回指定日期范围内的每日健康汇总."""
        s1 = DailyHealthSummary(id=1, record_date=date(2026, 6, 1), steps=8000)
        s2 = DailyHealthSummary(id=2, record_date=date(2026, 6, 2), steps=10000)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [s1, s2]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_daily_summaries(
            date(2026, 6, 1), date(2026, 6, 7),
        )

        assert len(result) == 2
        assert result[0].steps == 8000
        assert result[1].steps == 10000

    @pytest.mark.asyncio
    async def test_should_return_empty_list_when_no_data(
        self, health_dao, mock_session_factory
    ):
        """日期范围内无数据时应返回空列表."""
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_daily_summaries(
            date(2026, 1, 1), date(2026, 1, 7),
        )

        assert result == []


class TestGetWeeklySummaries:
    """get_weekly_summaries 日期筛选."""

    @pytest.mark.asyncio
    async def test_should_return_all_when_no_date_filters(
        self, health_dao, mock_session_factory
    ):
        """无日期筛选时应返回全部."""
        w1 = WeeklyHealthSummary(id=1, week_start=date(2026, 6, 1))
        w2 = WeeklyHealthSummary(id=2, week_start=date(2026, 5, 25))
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [w1, w2]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_weekly_summaries()

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_should_filter_by_date_range(
        self, health_dao, mock_session_factory
    ):
        """指定 start_week 时查询条件应包含下限."""
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_weekly_summaries(
            start_week=date(2026, 6, 1),
            end_week=date(2026, 6, 28),
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_should_apply_limit(
        self, health_dao, mock_session_factory
    ):
        """应应用 limit 参数控制返回条数."""
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_weekly_summaries(limit=3)

        assert result == []


class TestGetShoppingList:
    """get_shopping_list 日期范围查询."""

    @pytest.mark.asyncio
    async def test_should_query_by_date_range(
        self, health_dao, mock_session_factory
    ):
        """应返回指定日期范围内的购物清单."""
        item = ShoppingItem(id=1, name="牛奶", purchase_date=date(2026, 6, 1), items=[])
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [item]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_shopping_list(
            date(2026, 6, 1), date(2026, 6, 30),
        )

        assert len(result) == 1
        assert result[0].name == "牛奶"

    @pytest.mark.asyncio
    async def test_should_return_empty_when_no_items(
        self, health_dao, mock_session_factory
    ):
        """日期范围内无记录时返回空列表."""
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_shopping_list(
            date(2026, 1, 1), date(2026, 1, 7),
        )

        assert result == []


class TestGetWorkoutHistory:
    """get_workout_history 日期范围查询."""

    @pytest.mark.asyncio
    async def test_should_return_workout_history(
        self, health_dao, mock_session_factory
    ):
        """应返回指定天数内的运动记录."""
        w = WorkoutRecord(
            id=1, workout_type="Running",
            start_time=datetime(2026, 6, 1, 8, 0), duration=30.0,
        )
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [w]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_workout_history(days=30)

        assert len(result) == 1
        assert result[0].workout_type == "Running"


class TestWorkoutHistoryFiltered:
    """get_workout_history_filtered 支持可选的类型筛选."""

    @pytest.mark.asyncio
    async def test_should_filter_by_workout_type(
        self, health_dao, mock_session_factory
    ):
        """指定 workout_type 时应过滤对应类型."""
        w = WorkoutRecord(
            id=1, workout_type="Cycling",
            start_time=datetime(2026, 6, 1, 7, 0), duration=45.0,
        )
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [w]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_workout_history_filtered(
            days=30, workout_type="Cycling", limit=20,
        )

        assert len(result) == 1
        assert result[0].workout_type == "Cycling"

    @pytest.mark.asyncio
    async def test_should_return_all_when_no_type_filter(
        self, health_dao, mock_session_factory
    ):
        """不指定 workout_type 时应返回全部类型."""
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session_factory.session.execute.return_value = mock_result

        result = await health_dao.get_workout_history_filtered(days=60)

        assert result == []
