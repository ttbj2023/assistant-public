"""健康数据提取模型单元测试.

覆盖 inference/health_data_extraction/models/ 的验证逻辑:
- MedicalReportModel: ISO8601日期, report_type验证
- WorkoutRecordModel: ISO8601日期, workout_type格式化
- FoodProductModel: nutrition_per_100g, allergens
"""

from __future__ import annotations

import pytest

from src.inference.health_data_extraction.models.food_product import (
    FoodProductModel,
    NutritionPer100gModel,
)
from src.inference.health_data_extraction.models.medical_report import (
    MedicalReportModel,
)
from src.inference.health_data_extraction.models.workout_record import (
    WorkoutRecordModel,
)


class TestMedicalReportModel:
    """体检报告模型测试."""

    def test_invalid_report_type_raises(self) -> None:
        with pytest.raises(Exception, match="Invalid report_type"):
            MedicalReportModel(
                report_date="2025-01-15",
                report_data={},
                report_type="invalid_type",
            )

    def test_invalid_date_raises(self) -> None:
        with pytest.raises(Exception, match="Invalid ISO 8601"):
            MedicalReportModel(
                report_date="not-a-date",
                report_data={},
            )

    def test_z_suffix_date(self) -> None:
        report = MedicalReportModel(
            report_date="2025-01-15T10:30:00Z",
            report_data={},
        )
        assert report.report_date == "2025-01-15T10:30:00Z"


class TestWorkoutRecordModel:
    """运动记录模型测试."""

    def test_valid_workout(self) -> None:
        workout = WorkoutRecordModel(
            workout_type="running",
            duration_minutes=30.0,
            distance=5000.0,
            start_time="2025-01-15T18:00:00",
        )
        assert workout.workout_type == "Running"
        assert workout.duration_minutes == 30.0

    def test_workout_type_capitalized(self) -> None:
        workout = WorkoutRecordModel(
            workout_type="swimming",
            duration_minutes=45,
            start_time="2025-01-15T08:00:00",
        )
        assert workout.workout_type == "Swimming"

    def test_zero_duration_raises(self) -> None:
        with pytest.raises(Exception):
            WorkoutRecordModel(
                workout_type="run",
                duration_minutes=0,
                start_time="2025-01-15T18:00:00",
            )

    def test_negative_distance_raises(self) -> None:
        with pytest.raises(Exception):
            WorkoutRecordModel(
                workout_type="run",
                duration_minutes=30,
                distance=-1.0,
                start_time="2025-01-15T18:00:00",
            )

    def test_invalid_start_time_raises(self) -> None:
        with pytest.raises(Exception, match="Invalid ISO 8601"):
            WorkoutRecordModel(
                workout_type="run",
                duration_minutes=30,
                start_time="invalid",
            )


class TestFoodProductModel:
    """食品包装模型测试."""

    def test_negative_weight_raises(self) -> None:
        with pytest.raises(Exception):
            FoodProductModel(
                product_id="test",
                name="test",
                weight_per_unit=-100,
            )


class TestNutritionPer100gModel:
    """营养成分模型测试."""

    def test_negative_values_raises(self) -> None:
        with pytest.raises(Exception):
            NutritionPer100gModel(protein=-1.0)
