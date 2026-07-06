"""体检报告验证模型."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator


class MedicalReportModel(BaseModel):
    """体检报告验证模型."""

    report_date: str = Field(..., description="报告日期(ISO 8601)")
    report_data: dict[str, object] = Field(..., description="报告数据(扁平JSON)")
    report_type: str | None = Field(None, description="报告类型(routine/specialized)")

    @field_validator("report_date")
    @classmethod
    def validate_iso8601(cls, v: str) -> str:
        """验证ISO 8601格式."""
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Invalid ISO 8601 format: {v}") from None
        return v

    @field_validator("report_type")
    @classmethod
    def validate_report_type(cls, v: str | None) -> str | None:
        """验证报告类型."""
        if v is not None and v not in {"routine", "specialized"}:
            raise ValueError(
                f"Invalid report_type: {v}. Must be 'routine' or 'specialized'",
            )
        return v

    class Config:
        json_schema_extra: ClassVar[dict] = {
            "example": {
                "report_date": "2025-01-15T10:30:00",
                "report_data": {
                    "blood_pressure": "120/80",
                    "heart_rate": 72,
                    "blood_sugar": 5.2,
                },
                "report_type": "routine",
            },
        }
