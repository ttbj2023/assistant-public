"""健康运动记录工具 - list_workout_records.

查询运动列表或运动统计汇总, 支持类型筛选.
"""

from __future__ import annotations

import logging
from typing import Any, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.health_data_helpers import HealthDataServiceAccessor
from src.tools.shared.tool_runtime import format_tool_error, sync_runnable

logger = logging.getLogger(__name__)


class ListWorkoutRecordsRequest(BaseModel):
    """健康运动记录请求模型."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    mode: str | None = Field(
        default="list",
        description="查询模式: list(运动列表, 默认) 或 stats(统计汇总)",
    )
    days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="查询天数, list 默认30, stats 默认90",
    )
    workout_type: str | None = Field(
        default=None,
        description="运动类型筛选, 如 户外步行/太极拳/户外骑行等",
    )
    limit: int | None = Field(
        default=20,
        ge=1,
        le=100,
        description="返回记录数上限, 仅 list 模式有效",
    )


@sync_runnable
class ListWorkoutRecordsTool(BaseTool):
    """健康运动记录."""

    name: str = "list_workout_records"
    description: str = (
        "查询运动记录. 参数: mode=list/stats, days, workout_type(类型筛选), limit(list 模式). "
        "list 返回每次运动的时间/类型/时长/距离/卡路里/心率."
    )
    args_schema: type[ListWorkoutRecordsRequest] = ListWorkoutRecordsRequest

    def _get_accessor(self) -> HealthDataServiceAccessor:
        if not hasattr(self, "_health_acc"):
            acc = HealthDataServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_health_acc", acc)
        return self._health_acc

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = ListWorkoutRecordsRequest(**kwargs)
            if request.mode == "stats":
                return await self._run_stats(request)
            return await self._run_list(request)
        except Exception as e:
            logger.error("健康运动记录查询失败: %s", e)
            return format_tool_error(e)

    async def _run_list(self, request: ListWorkoutRecordsRequest) -> str:
        service = await self._get_accessor().get_service()
        days = min(request.days or 30, 365)
        workout_type = request.workout_type
        limit = request.limit or 20

        records = await service.get_workout_history_filtered(
            days=days,
            workout_type=workout_type,
            limit=limit,
        )

        if not records:
            type_hint = f" {workout_type}" if workout_type else ""
            return f"最近{days}天无{type_hint}运动记录"

        header = f"运动记录(最近{days}天"
        if workout_type:
            header += f", 类型: {workout_type}"
        header += f", {len(records)}条):"

        lines = [header]
        for r in records[:limit]:
            parts = [f"- {r.start_time.strftime('%Y-%m-%d %H:%M')}"]
            parts.append(f"{r.workout_type}")
            parts.append(f"{r.duration:.0f}min")
            if r.distance:
                parts.append(f"{r.distance:.1f}km")
            if r.calories:
                parts.append(f"{r.calories:.0f}kcal")
            if r.heart_rate_avg:
                parts.append(f"心率{r.heart_rate_avg:.0f}")
            lines.append(", ".join(parts))

        return "\n".join(lines)

    async def _run_stats(self, request: ListWorkoutRecordsRequest) -> str:
        service = await self._get_accessor().get_service()
        days = request.days or 90
        workout_type = request.workout_type

        stats = await service.get_workout_stats(days=days, workout_type=workout_type)

        if stats.get("status") != "success" or stats.get("total_count", 0) == 0:
            type_hint = f" {workout_type}" if workout_type else ""
            return f"最近{days}天无{type_hint}运动记录"

        lines = [f"运动统计(最近{days}天):"]
        lines.append(
            f"- 总计: {stats['total_count']}次, {stats['total_duration_minutes']:.0f}分钟",
        )
        lines.append(f"- 频率: {stats['freq_per_week']}次/周")

        type_dist = stats.get("type_distribution", {})
        if type_dist:
            lines.append("- 类型分布:")
            for wtype, info in type_dist.items():
                lines.append(
                    f"  {wtype}: {info['count']}次, {info['duration']:.0f}分钟",
                )

        return "\n".join(lines)


__all__ = ["ListWorkoutRecordsTool"]
