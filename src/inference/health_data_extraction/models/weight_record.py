"""体重记录验证模型."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator


class WeightRecordModel(BaseModel):
    """体重记录验证模型."""

    timestamp: str = Field(..., description="记录时间(ISO 8601)")
    weight: float = Field(..., ge=20.0, le=300.0, description="体重(kg)")
    body_fat_percentage: float | None = Field(
        None,
        ge=3.0,
        le=50.0,
        description="体脂率(%)",
    )
    muscle_mass: float | None = Field(None, ge=10.0, le=150.0, description="肌肉量(kg)")

    @field_validator("timestamp")
    @classmethod
    def validate_iso8601(cls, v: str) -> str:
        """验证ISO 8601格式."""
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Invalid ISO 8601 format: {v}") from None
        return v

    class Config:
        json_schema_extra: ClassVar[dict] = {
            "example": {
                "timestamp": "2025-01-15T08:00:00",
                "weight": 75.5,
                "body_fat_percentage": 22.0,
                "muscle_mass": 55.0,
            },
        }
