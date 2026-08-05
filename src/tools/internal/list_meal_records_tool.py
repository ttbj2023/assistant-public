"""健康饮食记录工具 - list_meal_records.

查询指定单日或最近N天的饮食记录和营养摄入.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.health_data_helpers import (
    HealthDataServiceAccessor,
    format_nutrition_detail,
    user_today,
)
from src.tools.shared.tool_runtime import format_tool_error, sync_runnable

logger = logging.getLogger(__name__)


class ListMealRecordsRequest(BaseModel):
    """健康饮食记录请求模型."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="查询最近N天, 默认7, 最大90. 与 target_date 互斥.",
    )
    target_date: str | None = Field(
        default=None,
        description="指定日期(YYYY-MM-DD), 查询单日饮食明细",
    )


@sync_runnable
class ListMealRecordsTool(BaseTool):
    """健康饮食记录."""

    name: str = "list_meal_records"
    description: str = (
        "查询饮食记录和营养摄入. 参数: target_date 查单日; 不提供则查最近 days 天(默认7). "
        "单日输出各餐明细与食物项."
    )
    args_schema: type[ListMealRecordsRequest] = ListMealRecordsRequest

    def _get_accessor(self) -> HealthDataServiceAccessor:
        if not hasattr(self, "_health_acc"):
            acc = HealthDataServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_health_acc", acc)
        return self._health_acc

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = ListMealRecordsRequest(**kwargs)
            service = await self._get_accessor().get_service()

            if request.target_date:
                target = date.fromisoformat(request.target_date)
                nutrition = await service.get_nutrition_summary(target)
                if nutrition.get("status") == "no_data":
                    return f"{request.target_date} 无饮食记录"
                return format_nutrition_detail(target, nutrition)

            days = min(request.days or 7, 90)
            end_date = user_today(self.user_id)
            start_date = end_date - timedelta(days=days - 1)

            range_data = await service.get_nutrition_range(start_date, end_date)

            lines = [f"饮食记录(最近{days}天):"]
            has_data = False
            for i in range(days):
                d = end_date - timedelta(days=i)
                nutrition = range_data.get(d.isoformat())
                if not nutrition:
                    continue
                has_data = True
                cal = nutrition.get("calories", 0)
                protein = nutrition.get("protein", 0)
                carbs = nutrition.get("carbs", 0)
                fat = nutrition.get("fat", 0)
                meals = nutrition.get("meal_count", 0)
                lines.append(
                    f"- {d}: {meals}餐, "
                    f"{cal:.0f}kcal (蛋白{protein:.0f}g 碳水{carbs:.0f}g 脂肪{fat:.0f}g)",
                )

            if not has_data:
                return f"最近{days}天无饮食记录"
            return "\n".join(lines)
        except Exception as e:
            logger.error("健康饮食记录查询失败: %s", e)
            return format_tool_error(e)


__all__ = ["ListMealRecordsTool"]
