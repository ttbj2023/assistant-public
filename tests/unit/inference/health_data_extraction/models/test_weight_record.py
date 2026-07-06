"""健康数据提取模型测试 - WeightRecordModel.

测试体重记录验证模型的字段验证和约束.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.inference.health_data_extraction.models.weight_record import WeightRecordModel


class TestWeightRecordModel:
    def test_should_create_valid_record(self):
        """测试应创建有效体重记录."""
        record = WeightRecordModel(
            timestamp="2025-01-15T08:00:00",
            weight=75.5,
        )
        assert record.weight == 75.5
        assert record.body_fat_percentage is None
        assert record.muscle_mass is None

    def test_should_create_with_all_fields(self):
        """测试应创建包含所有字段的记录."""
        record = WeightRecordModel(
            timestamp="2025-01-15T08:00:00",
            weight=75.5,
            body_fat_percentage=22.0,
            muscle_mass=55.0,
        )
        assert record.body_fat_percentage == 22.0
        assert record.muscle_mass == 55.0

    def test_should_reject_weight_below_minimum(self):
        """测试应拒绝低于最小值的体重."""
        with pytest.raises(ValidationError):
            WeightRecordModel(timestamp="2025-01-15T08:00:00", weight=10.0)

    def test_should_reject_weight_above_maximum(self):
        """测试应拒绝高于最大值的体重."""
        with pytest.raises(ValidationError):
            WeightRecordModel(timestamp="2025-01-15T08:00:00", weight=500.0)

    def test_should_reject_invalid_timestamp(self):
        """测试应拒绝无效时间戳."""
        with pytest.raises(ValidationError):
            WeightRecordModel(timestamp="not-a-date", weight=70.0)

    def test_should_accept_utc_timestamp(self):
        """测试应接受UTC时间戳."""
        record = WeightRecordModel(
            timestamp="2025-01-15T08:00:00Z",
            weight=70.0,
        )
        assert record.timestamp == "2025-01-15T08:00:00Z"

    def test_should_reject_invalid_body_fat(self):
        """测试应拒绝无效体脂率."""
        with pytest.raises(ValidationError):
            WeightRecordModel(
                timestamp="2025-01-15T08:00:00",
                weight=70.0,
                body_fat_percentage=60.0,
            )

    def test_should_reject_invalid_muscle_mass(self):
        """测试应拒绝无效肌肉量."""
        with pytest.raises(ValidationError):
            WeightRecordModel(
                timestamp="2025-01-15T08:00:00",
                weight=70.0,
                muscle_mass=200.0,
            )
