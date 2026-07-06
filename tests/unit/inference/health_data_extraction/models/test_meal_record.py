"""健康数据提取模型测试 - MealRecordModel.

测试饮食记录验证模型的字段验证和约束.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.inference.health_data_extraction.models.meal_record import (
    MealItemModel,
    MealRecordModel,
)


class TestMealItemModel:
    def test_should_create_valid_item(self):
        """测试应创建有效饮食项."""
        item = MealItemModel(name="米饭", quantity="一碗")
        assert item.name == "米饭"
        assert item.quantity == "一碗"

    def test_should_use_default_quantity(self):
        """测试应使用默认数量."""
        item = MealItemModel(name="鸡蛋")
        assert item.quantity == "1份"


class TestMealRecordModel:
    def test_should_create_valid_record(self):
        """测试应创建有效饮食记录."""
        record = MealRecordModel(
            meal_date="2025-01-15",
            items=[
                MealItemModel(name="米饭", quantity="一碗"),
                MealItemModel(name="鸡蛋", quantity="2个"),
            ],
        )
        assert record.meal_date == "2025-01-15"
        assert len(record.items) == 2

    def test_should_accept_all_meal_types(self):
        """测试应接受所有合法餐型."""
        for meal_type in ["breakfast", "lunch", "dinner", "snack"]:
            record = MealRecordModel(
                meal_type=meal_type,
                meal_date="2025-01-15",
                items=[MealItemModel(name="test")],
            )
            assert record.meal_type == meal_type

    def test_should_convert_invalid_meal_type_to_none(self):
        """测试应将无效餐型转为None."""
        record = MealRecordModel(
            meal_type="invalid_type",
            meal_date="2025-01-15",
            items=[MealItemModel(name="test")],
        )
        assert record.meal_type is None

    def test_should_reject_invalid_date_format(self):
        """测试应拒绝无效日期格式."""
        with pytest.raises(ValidationError):
            MealRecordModel(
                meal_date="01-15-2025",
                items=[MealItemModel(name="test")],
            )

    def test_should_reject_empty_items(self):
        """测试应拒绝空食物列表."""
        with pytest.raises(ValidationError):
            MealRecordModel(
                meal_date="2025-01-15",
                items=[],
            )

    def test_should_accept_optional_fields_as_none(self):
        """测试可选字段应为None."""
        record = MealRecordModel(
            meal_date="2025-01-15",
            items=[MealItemModel(name="test")],
        )
        assert record.meal_type is None
        assert record.meal_time is None
        assert record.notes is None

    def test_should_include_notes(self):
        """测试应包含备注."""
        record = MealRecordModel(
            meal_date="2025-01-15",
            items=[MealItemModel(name="test")],
            notes="午餐在外就餐",
        )
        assert record.notes == "午餐在外就餐"
