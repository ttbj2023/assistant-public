"""健康时段对比工具 - compare_health_periods.

查询单指标周环比或月环比, 返回均值、变化百分比和方向.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.health_data_helpers import (
    METRIC_LABELS,
    HealthDataServiceAccessor,
    first_day_of_month_n_ago,
    user_today,
)
from src.tools.shared.tool_runtime import format_tool_error, sync_runnable

logger = logging.getLogger(__name__)


class CompareHealthPeriodsRequest(BaseModel):
    """健康时段对比请求模型."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    metric: str = Field(
        ...,
        description="指标名, 如 steps, body_mass_kg, sleep_duration_hours",
    )
    period_type: str | None = Field(
        default="week",
        description="时段类型: week(默认) 或 month",
    )
    period_offset: int = Field(
        default=0,
        ge=0,
        description="时段偏移: 0=当前, 1=上一期, 2=上两期",
    )


@sync_runnable
class CompareHealthPeriodsTool(BaseTool):
    """健康时段对比(周环比/月环比)."""

    name: str = "compare_health_periods"
    description: str = (
        "查询单指标时段对比(周环比/月环比). "
        "参数: metric(必需), period_type=week/month, period_offset=0/1/2."
    )
    args_schema: type[CompareHealthPeriodsRequest] = CompareHealthPeriodsRequest

    def _get_accessor(self) -> HealthDataServiceAccessor:
        if not hasattr(self, "_health_acc"):
            acc = HealthDataServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_health_acc", acc)
        return self._health_acc

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = CompareHealthPeriodsRequest(**kwargs)
            metric = request.metric

            label = METRIC_LABELS.get(metric, metric)
            service = await self._get_accessor().get_service()

            p1_start, p1_end, p2_start, p2_end, period_label_1, period_label_2 = (
                self._compute_periods(
                    request.period_type or "week", request.period_offset or 0
                )
            )

            result = await service.get_metric_comparison(
                metric,
                p1_start,
                p1_end,
                p2_start,
                p2_end,
            )

            lines = [f"{label}时段对比:"]

            p1 = result.get("period1", {})
            p2 = result.get("period2", {})

            if p1.get("count", 0) == 0 and p2.get("count", 0) == 0:
                return f"对比时段内均无{label}数据"

            if p1.get("avg") is not None:
                lines.append(
                    f"- {period_label_1}({p1['start']}~{p1['end']}): "
                    f"均值{p1['avg']:.1f}, {p1['count']}个数据点",
                )
            else:
                lines.append(f"- {period_label_1}({p1['start']}~{p1['end']}): 无数据")

            if p2.get("avg") is not None:
                lines.append(
                    f"- {period_label_2}({p2['start']}~{p2['end']}): "
                    f"均值{p2['avg']:.1f}, {p2['count']}个数据点",
                )
            else:
                lines.append(f"- {period_label_2}({p2['start']}~{p2['end']}): 无数据")

            change_pct = result.get("change_pct")
            direction = result.get("direction", "no_data")
            if change_pct is not None:
                arrow = {"up": "↑", "down": "↓", "stable": "→"}.get(direction, "")
                lines.append(f"- 变化: {arrow} {change_pct:+.1f}%")

            return "\n".join(lines)
        except Exception as e:
            logger.error("健康时段对比查询失败: %s", e)
            return format_tool_error(e)

    def _compute_periods(
        self,
        period_type: str,
        offset: int,
    ) -> tuple[date, date, date, date, str, str]:
        """计算对比的两个时段."""
        today = user_today(self.user_id)

        if period_type == "month":
            this_month_start = today.replace(day=1)
            if offset == 0:
                p1_start = this_month_start
                p1_end = today
            else:
                p1_start = first_day_of_month_n_ago(today, offset)
                p1_end = first_day_of_month_n_ago(today, offset - 1) - timedelta(days=1)

            p2_offset = offset + 1
            p2_end = p1_start - timedelta(days=1)
            p2_start = p2_end.replace(day=1)

            period_label_1 = "本月" if offset == 0 else f"{offset}个月前"
            period_label_2 = f"{p2_offset}个月前"
        else:
            weekday = today.weekday()
            this_monday = today - timedelta(days=weekday)

            current_monday = this_monday - timedelta(weeks=offset)
            p1_start = current_monday
            p1_end = min(current_monday + timedelta(days=6), today)

            prev_monday = current_monday - timedelta(weeks=1)
            p2_start = prev_monday
            p2_end = prev_monday + timedelta(days=6)

            period_label_1 = "本周" if offset == 0 else f"{offset}周前"
            period_label_2 = f"{offset + 1}周前"

        return p1_start, p1_end, p2_start, p2_end, period_label_1, period_label_2


__all__ = ["CompareHealthPeriodsTool"]
