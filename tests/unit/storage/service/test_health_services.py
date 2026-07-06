"""健康服务单元测试.

测试HealthDataService的功能.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.storage.models.health_data import DailyHealthSummary, MedicalReport
from src.storage.service.health_service import HealthDataService


@pytest.fixture
def mock_session_factory():

    class MockSessionFactory:
        def __call__(self):
            return self

    return MockSessionFactory()


@pytest.fixture
def health_service(mock_session_factory):
    """创建HealthDataService实例."""
    return HealthDataService(mock_session_factory)


@pytest.fixture
def sample_daily_summary():
    """创建示例DailyHealthSummary."""
    return DailyHealthSummary(
        record_date=date(2026, 1, 15),
        steps=8000,
        body_mass_kg=70.5,
        sleep_hours=7.5,
    )


@pytest.fixture
def sample_medical_report():
    """创建示例MedicalReport."""
    return MedicalReport(
        report_date=date(2026, 1, 10),
        report_type="annual",
        report_data={"blood_pressure": "120/80", "heart_rate": 72},
    )


class TestHealthDataService:
    @pytest.mark.asyncio
    async def test_analyze_health_status_should_aggregate_data(self, health_service):
        """测试健康状态分析应该聚合数据."""
        health_service.health_dao.get_latest_report = AsyncMock(return_value=None)
        health_service.health_dao.get_latest_daily_summaries = AsyncMock(
            return_value=[]
        )
        health_service.health_dao.get_weekly_activity_summary = AsyncMock(
            return_value={"status": "success", "total_workouts": 2}
        )

        result = await health_service.analyze_health_status()

        assert result["status"] == "success"
        assert result["activity_summary"]["total_workouts"] == 2
        assert "health_score" in result


class TestHealthDataServiceScoreCalculation:
    """测试健康评分计算逻辑."""

    @pytest.mark.asyncio
    async def test_calculate_health_score_max_score(
        self, health_service, sample_medical_report, sample_daily_summary
    ):
        """测试健康评分: 数据完整时应得满分."""
        score = await health_service._calculate_health_score(
            medical_report=sample_medical_report,
            daily_summaries=[sample_daily_summary],
            activity_summary={"status": "success", "total_workouts": 5},
        )
        assert score == 100

    @pytest.mark.asyncio
    async def test_calculate_health_score_no_data(self, health_service):
        """测试健康评分: 无数据时得分应为0."""
        score = await health_service._calculate_health_score(
            medical_report=None,
            daily_summaries=[],
            activity_summary={},
        )
        assert score == 0

    @pytest.mark.asyncio
    async def test_calculate_health_score_partial_data(
        self, health_service, sample_daily_summary
    ):
        """测试健康评分: 部分数据应正确计算."""
        score = await health_service._calculate_health_score(
            medical_report=None,
            daily_summaries=[sample_daily_summary],
            activity_summary={"status": "success", "total_workouts": 1},
        )
        assert score == 50  # 30 (daily) + 20 (1 workout)

    @pytest.mark.asyncio
    async def test_calculate_health_score_many_workouts(self, health_service):
        """测试健康评分: 运动次数超过3次应得满分运动分."""
        score = await health_service._calculate_health_score(
            medical_report=None,
            daily_summaries=[],
            activity_summary={"status": "success", "total_workouts": 10},
        )
        assert score == 40  # 仅运动满分


class TestHealthDataServiceRecommendations:
    """测试健康建议生成逻辑."""

    @pytest.mark.asyncio
    async def test_recommendations_no_activity(self, health_service):
        """测试建议: 无运动时应建议开始运动."""
        recs = await health_service._generate_health_recommendations(
            medical_report=None,
            daily_summaries=[],
            activity_summary={"status": "success", "total_workouts": 0},
        )
        assert any("规律运动" in r for r in recs)

    @pytest.mark.asyncio
    async def test_recommendations_few_workouts(self, health_service):
        """测试建议: 运动不足时应建议增加频率."""
        recs = await health_service._generate_health_recommendations(
            medical_report=None,
            daily_summaries=[],
            activity_summary={"status": "success", "total_workouts": 1},
        )
        assert any("增加运动频率" in r for r in recs)

    @pytest.mark.asyncio
    async def test_recommendations_with_medical_report(
        self, health_service, sample_medical_report
    ):
        """测试建议: 有体检报告时应建议持续关注."""
        recs = await health_service._generate_health_recommendations(
            medical_report=sample_medical_report,
            daily_summaries=[],
            activity_summary={"status": "success", "total_workouts": 5},
        )
        assert any("体检" in r for r in recs)

    @pytest.mark.asyncio
    async def test_recommendations_low_steps_days(
        self, health_service, sample_daily_summary
    ):
        """测试建议: 低步数天数超过50%时应建议增加活动量."""
        low_step_summaries = [
            DailyHealthSummary(
                record_date=date(2026, 1, i + 1),
                steps=500,
                body_mass_kg=None,
                sleep_hours=7,
            )
            for i in range(10)
        ]
        recs = await health_service._generate_health_recommendations(
            medical_report=None,
            daily_summaries=low_step_summaries,
            activity_summary={"status": "success", "total_workouts": 3},
        )
        assert any("步数低于1000" in r for r in recs)

    @pytest.mark.asyncio
    async def test_recommendations_no_low_steps(self, health_service):
        """测试建议: 步数正常时不应建议增加活动量."""
        high_step_summaries = [
            DailyHealthSummary(
                record_date=date(2026, 1, i + 1),
                steps=5000,
                body_mass_kg=None,
                sleep_hours=7,
            )
            for i in range(10)
        ]
        recs = await health_service._generate_health_recommendations(
            medical_report=None,
            daily_summaries=high_step_summaries,
            activity_summary={"status": "success", "total_workouts": 3},
        )
        assert not any("步数低于1000" in r for r in recs)


class TestHealthDataServiceWeightTrend:
    """测试体重趋势分析."""

    def test_weight_trend_no_data(self, health_service):
        """测试体重趋势: 无数据时应返回no_data."""
        result = health_service._analyze_weight_trend_from_daily([])
        assert result["trend"] == "no_data"
        assert result["latest_weight"] is None
        assert result["records_count"] == 0

    def test_weight_trend_single_record(self, health_service, sample_daily_summary):
        """测试体重趋势: 单条记录时应返回stable."""
        result = health_service._analyze_weight_trend_from_daily([sample_daily_summary])
        assert result["trend"] == "stable"
        assert result["latest_weight"] == 70.5
        assert result["records_count"] == 1

    def test_weight_trend_increasing(self, health_service):
        """测试体重趋势: 体重增加超过1kg时应返回increasing."""
        # index 0 是最近的(最重), index -1 是最旧的(最轻)
        summaries = [
            DailyHealthSummary(
                record_date=date(2026, 1, 5 - i),
                steps=8000,
                body_mass_kg=76.5 - i,
                sleep_hours=7,
            )
            for i in range(5)
        ]
        result = health_service._analyze_weight_trend_from_daily(summaries)
        assert result["trend"] == "increasing"

    def test_weight_trend_decreasing(self, health_service):
        """测试体重趋势: 体重减少超过1kg时应返回decreasing."""
        # index 0 是最近的(最轻), index -1 是最旧的(最重)
        summaries = [
            DailyHealthSummary(
                record_date=date(2026, 1, 5 - i),
                steps=8000,
                body_mass_kg=71.5 + i,
                sleep_hours=7,
            )
            for i in range(5)
        ]
        result = health_service._analyze_weight_trend_from_daily(summaries)
        assert result["trend"] == "decreasing"

    def test_weight_trend_stable(self, health_service):
        """测试体重趋势: 体重变化不超过1kg时应返回stable."""
        summaries = [
            DailyHealthSummary(
                record_date=date(2026, 1, 5 - i),
                steps=8000,
                body_mass_kg=70.4 - i * 0.1,
                sleep_hours=7,
            )
            for i in range(5)
        ]
        result = health_service._analyze_weight_trend_from_daily(summaries)
        assert result["trend"] == "stable"


class TestHealthDataServiceQueryPassthrough:
    """测试DAO透传查询方法."""

    @pytest.mark.asyncio
    async def test_get_report_detail_should_return_success(
        self, health_service, sample_medical_report
    ):
        """测试报告详情: 有数据时应返回成功."""
        health_service.health_dao.get_latest_report = AsyncMock(
            return_value=sample_medical_report
        )
        health_service.health_dao.analyze_report_trends = AsyncMock(return_value=[])
        result = await health_service.get_report_detail()
        assert result["status"] == "success"
        assert result["latest"]["report_date"] == "2026-01-10"


class TestHealthDataServiceHealthCheck:
    """测试服务健康检查."""

    @pytest.mark.asyncio
    async def test_health_check_should_return_healthy(self, health_service):
        """测试健康检查: 所有表正常时应返回healthy."""
        health_service.health_dao.health_check = AsyncMock(
            return_value={"weight_records": True, "meals": True}
        )
        result = await health_service.health_check()
        assert result["status"] == "healthy"
        assert result["database_connected"] is True

    @pytest.mark.asyncio
    async def test_health_check_should_return_degraded(self, health_service):
        """测试健康检查: 部分表异常时应返回degraded."""
        health_service.health_dao.health_check = AsyncMock(
            return_value={"weight_records": True, "meals": False}
        )
        result = await health_service.health_check()
        assert result["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_health_check_should_return_unhealthy_on_exception(
        self, health_service
    ):
        """测试健康检查: 异常时应返回unhealthy."""
        health_service.health_dao.health_check = AsyncMock(
            side_effect=Exception("DB connection failed")
        )
        result = await health_service.health_check()
        assert result["status"] == "unhealthy"
        assert result["database_connected"] is False
