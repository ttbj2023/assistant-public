"""运动记录验证模型."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator


class WorkoutRecordModel(BaseModel):
    """运动记录验证模型."""

    workout_type: str = Field(..., description="运动类型")
    duration_minutes: float = Field(..., gt=0, description="持续时间(分钟)")
    distance: float | None = Field(None, ge=0, description="距离(米)")
    calories: float | None = Field(None, ge=0, description="消耗卡路里")
    heart_rate_avg: float | None = Field(None, ge=0, description="平均心率")
    heart_rate_max: float | None = Field(None, ge=0, description="最大心率")
    start_time: str = Field(..., description="开始时间(ISO 8601)")

    @field_validator("start_time")
    @classmethod
    def validate_iso8601(cls, v: str) -> str:
        """验证ISO 8601格式."""
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Invalid ISO 8601 format: {v}") from None
        return v

    @field_validator("workout_type")
    @classmethod
    def validate_workout_type(cls, v: str) -> str:
        """格式化运动类型名称."""
        return v.capitalize()

    class Config:
        json_schema_extra: ClassVar[dict] = {
            "example": {
                "workout_type": "Running",
                "duration": 1800,
                "distance": 5000.0,
                "calories": 300.0,
                "start_time": "2025-01-15T18:00:00",
            },
        }
