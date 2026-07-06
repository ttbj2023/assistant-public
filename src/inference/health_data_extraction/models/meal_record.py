"""饮食记录验证模型 - 只记录食物名称和数量, 不推断营养数据."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator


class MealItemModel(BaseModel):
    """饮食项模型 - 只记名称和数量."""

    name: str = Field(..., description="食物名称")
    quantity: str = Field("1份", description="数量描述(如: 1碗, 2个, 200g)")

    class Config:
        json_schema_extra: ClassVar[dict] = {
            "example": {
                "name": "糙米饭",
                "quantity": "一碗",
            },
        }


class MealRecordModel(BaseModel):
    """饮食记录验证模型 - 只记食物名称数量, 不推断营养."""

    meal_type: str | None = Field(
        None,
        description="餐型(breakfast/lunch/dinner/snack)",
    )
    meal_date: str = Field(..., description="用餐日期(YYYY-MM-DD)")
    meal_time: str | None = Field(None, description="用餐时间(HH:MM)")
    items: list[MealItemModel] = Field(..., description="饮食项列表", min_length=1)
    notes: str | None = Field(None, description="备注")

    @field_validator("meal_type")
    @classmethod
    def validate_meal_type(cls, v: str | None) -> str | None:
        """验证餐型."""
        if v is None:
            return v
        valid_types = ["breakfast", "lunch", "dinner", "snack"]
        if v not in valid_types:
            return None
        return v

    @field_validator("meal_date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        """验证日期格式."""
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Invalid date format: {v}. Must be YYYY-MM-DD") from None
        return v

    class Config:
        json_schema_extra: ClassVar[dict] = {
            "example": {
                "meal_type": "lunch",
                "meal_date": "2026-05-23",
                "items": [
                    {"name": "糙米饭", "quantity": "一碗"},
                    {"name": "清蒸豆腐", "quantity": "一份"},
                ],
                "notes": "午餐",
            },
        }
