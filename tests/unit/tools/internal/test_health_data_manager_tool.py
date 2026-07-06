"""健康数据管理工具单元测试.

测试 src/tools/internal/health_data_manager_tool.py 的功能:
- 初始化和元数据
- _arun action路由分发
- 参数校验(缺少metric等)
- 格式化输出辅助方法
- 不支持action的错误处理

Mock边界:
- Mock _get_service() 返回 Mock HealthDataService
- 保留真实action分发和格式化逻辑
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest

from src.tools.internal.health_data_manager_tool import (
    HealthDataManagerTool,
    _v,
)


@pytest.fixture
def mock_service():
    """创建Mock HealthDataService."""
    service = Mock()
    service.get_data_coverage = AsyncMock(
        return_value={
            "tables": {
                "daily_health_summary": 30,
                "workout_records": 5,
            },
            "daily": {
                "total": 30,
                "date_range": {"start": "2025-01-01", "end": "2025-01-30"},
            },
            "data_sources": {"external_import": 30},
            "latest_update": "2025-01-30",
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
    service.get_report_detail = AsyncMock(
        return_value={"status": "no_data", "message": "暂无体检报告"}
    )
    service.get_metric_comparison = AsyncMock(
        return_value={
            "period1": {
                "start": "2025-01-20",
                "end": "2025-01-26",
                "avg": 8000,
                "count": 7,
            },
            "period2": {
                "start": "2025-01-13",
                "end": "2025-01-19",
                "avg": 7500,
                "count": 7,
            },
            "change_pct": 6.7,
            "direction": "up",
        }
    )
    service.get_weekly_activity_summary = AsyncMock(return_value={"status": "error"})
    service.get_meals = AsyncMock(return_value=[])
    service.get_nutrition_summary = AsyncMock(
        return_value={"status": "no_data", "message": "该日期没有摄入记录"}
    )
    service.get_shopping_list = AsyncMock(return_value=[])
    return service


@pytest.fixture
def tool(test_user, test_thread_id, mock_service):
    """创建HealthDataManagerTool实例."""
    t = HealthDataManagerTool(
        user_id=test_user, thread_id=test_thread_id, agent_id="test-agent"
    )
    object.__setattr__(t, "_health_service", mock_service)
    return t


class TestHealthDataManagerToolActionRouting:
    def _make_kwargs(self, tool, **extra):
        """构造参数字典."""
        return extra

    @pytest.mark.asyncio
    async def test_unsupported_action_should_return_error(self, tool):
        """测试不支持的操作应返回错误信息."""
        result = await tool._arun(**self._make_kwargs(tool, action="invalid_action"))

        assert "不支持的操作" in result
        assert "invalid_action" in result

    @pytest.mark.asyncio
    async def test_get_overview_should_return_snapshot(self, tool, mock_service):
        """测试get_overview应返回健康快照."""
        result = await tool._arun(**self._make_kwargs(tool, action="get_overview"))

        assert "健康快照" in result
        mock_service.get_data_coverage.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_daily_with_target_date(self, tool, mock_service):
        """测试get_daily指定日期查询."""
        mock_summary = Mock()
        mock_summary.record_date = date(2025, 1, 15)
        mock_summary.steps = 8000
        mock_summary.active_energy_kcal = 350.0
        mock_summary.basal_energy_kcal = None
        mock_summary.distance_km = None
        mock_summary.apple_exercise_minutes = None
        mock_summary.stand_hours = None
        mock_summary.body_mass_kg = 70.5
        mock_summary.body_fat_pct = None
        mock_summary.muscle_mass_kg = None
        mock_summary.resting_hr_bpm = None
        mock_summary.hrv_ms = None
        mock_summary.vo2_max = None
        mock_summary.sleep_duration_hours = 7.5
        mock_summary.sleep_efficiency = None
        mock_summary.bed_time = None
        mock_summary.wake_time = None
        mock_summary.deep_sleep_minutes = None
        mock_summary.asleep_minutes = None
        mock_summary.rem_sleep_minutes = None
        mock_summary.core_sleep_minutes = None
        mock_summary.awake_minutes = None
        mock_summary.weight_7d_avg = None
        mock_summary.steps_7d_avg = None
        mock_summary.resting_hr_7d_avg = None
        mock_summary.hrv_7d_avg = None
        mock_summary.sleep_7d_avg = None
        mock_summary.sleep_efficiency_7d_avg = None
        mock_summary.exercise_7d_total = None
        mock_summary.avg_hr_bpm = None
        mock_summary.min_hr_bpm = None
        mock_summary.max_hr_bpm = None
        mock_summary.blood_oxygen_pct = None
        mock_summary.wrist_temperature = None
        mock_summary.respiratory_rate = None
        mock_service.get_daily_summary = AsyncMock(return_value=mock_summary)

        result = await tool._arun(
            **self._make_kwargs(tool, action="get_daily", target_date="2025-01-15")
        )

        assert "2025-01-15" in result
        assert "8000" in result

    @pytest.mark.asyncio
    async def test_get_daily_no_data(self, tool, mock_service):
        """测试get_daily无数据."""
        mock_service.get_daily_summaries = AsyncMock(return_value=[])

        result = await tool._arun(**self._make_kwargs(tool, action="get_daily", days=7))

        assert "无" in result

    @pytest.mark.asyncio
    async def test_get_trend_should_require_metric(self, tool):
        """测试get_trend缺少metric应返回错误提示."""
        result = await tool._arun(**self._make_kwargs(tool, action="get_trend"))

        assert "请指定metric参数" in result

    @pytest.mark.asyncio
    async def test_get_trend_with_data(self, tool, mock_service):
        """测试get_trend有数据时应返回趋势."""
        mock_service.get_metric_history = AsyncMock(
            return_value=[
                {"date": "2025-01-30", "value": 75.0},
                {"date": "2025-01-29", "value": 74.5},
                {"date": "2025-01-28", "value": 74.8},
            ]
        )

        result = await tool._arun(
            **self._make_kwargs(tool, action="get_trend", metric="body_mass_kg")
        )

        assert "体重" in result
        assert "75.0" in result

    @pytest.mark.asyncio
    async def test_get_comparison_should_require_metric(self, tool):
        """测试get_comparison缺少metric应返回错误提示."""
        result = await tool._arun(**self._make_kwargs(tool, action="get_comparison"))

        assert "请指定metric参数" in result

    @pytest.mark.asyncio
    async def test_get_comparison_with_data(self, tool, mock_service):
        """测试get_comparison有数据."""
        result = await tool._arun(
            **self._make_kwargs(tool, action="get_comparison", metric="steps")
        )

        assert "步数" in result
        assert "6.7%" in result

    @pytest.mark.asyncio
    async def test_get_overview_no_daily_data(self, tool, mock_service):
        """测试get_overview无每日数据时仍返回快照."""
        result = await tool._arun(**self._make_kwargs(tool, action="get_overview"))

        assert "健康快照" in result

    @pytest.mark.asyncio
    async def test_get_workout_no_data(self, tool, mock_service):
        """测试get_workout无数据."""
        result = await tool._arun(**self._make_kwargs(tool, action="get_workout"))

        assert "无" in result

    @pytest.mark.asyncio
    async def test_get_workout_stats_no_data(self, tool, mock_service):
        """测试get_workout stats模式无数据."""
        result = await tool._arun(
            **self._make_kwargs(tool, action="get_workout", mode="stats")
        )

        assert "无" in result

    @pytest.mark.asyncio
    async def test_get_report_no_data(self, tool, mock_service):
        """测试get_report无数据."""
        result = await tool._arun(**self._make_kwargs(tool, action="get_report"))

        assert "暂无" in result

    @pytest.mark.asyncio
    async def test_get_meals_no_data(self, tool, mock_service):
        """测试get_meals无数据."""
        result = await tool._arun(**self._make_kwargs(tool, action="get_meals", days=7))

        assert "无" in result

    @pytest.mark.asyncio
    async def test_get_shopping_no_data(self, tool, mock_service):
        """测试get_shopping无数据."""
        result = await tool._arun(
            **self._make_kwargs(tool, action="get_shopping", days=30)
        )

        assert "无" in result


class TestVHelper:
    def test_none_should_return_false(self):
        """测试None应返回False."""
        assert _v(None) is False

    def test_zero_should_return_false(self):
        """测试0应返回False."""
        assert _v(0) is False

    def test_positive_value(self):
        """测试正值应返回True."""
        assert _v(10) is True

    def test_float_value(self):
        """测试浮点值."""
        assert _v(3.14) is True
