"""健康指标趋势工具 - query_metric_trend.

查询单指标日维度或周维度趋势, 含均值/变化/极值/断档检测.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.health_data_helpers import (
    METRIC_LABELS,
    WEEKLY_METRIC_LABELS,
    HealthDataServiceAccessor,
)
from src.tools.shared.tool_runtime import format_tool_error, sync_runnable

logger = logging.getLogger(__name__)


class QueryMetricTrendRequest(BaseModel):
    """健康指标趋势请求模型."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    metric: str = Field(
        ...,
        description="指标名, 如 steps, body_mass_kg, resting_hr_bpm, hrv_ms, sleep_duration_hours, weight_7d_avg 等",
    )
    days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="查询天数(日趋势默认30, 周趋势默认12); 周趋势时代表周数",
    )
    period: str | None = Field(
        default="daily",
        description="时间粒度: daily(默认) 或 weekly",
    )


@sync_runnable
class QueryMetricTrendTool(BaseTool):
    """健康指标趋势."""

    name: str = "query_metric_trend"
    description: str = (
        "查询单指标趋势. 参数: metric(必需), days(默认30), period=daily/weekly. "
        "返回最新值、均值、变化量、最大/最小值、近期列表和数据断档提示."
    )
    args_schema: type[QueryMetricTrendRequest] = QueryMetricTrendRequest

    def _get_accessor(self) -> HealthDataServiceAccessor:
        if not hasattr(self, "_health_acc"):
            acc = HealthDataServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_health_acc", acc)
        return self._health_acc

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = QueryMetricTrendRequest(**kwargs)
            metric = request.metric
            period = request.period or "daily"
            if period == "weekly":
                return await self._run_weekly(metric, request.days or 12)

            return await self._run_daily(metric, request.days or 30)
        except Exception as e:
            logger.error("健康趋势查询失败: %s", e)
            return format_tool_error(e)

    async def _run_daily(self, metric: str, days: int) -> str:
        label = METRIC_LABELS.get(metric, metric)
        if metric not in METRIC_LABELS:
            return f"错误: 不支持的指标 '{metric}'"
        service = await self._get_accessor().get_service()

        history = await service.get_metric_history(metric, days=days)

        if not history:
            return f"最近{days}天无{label}数据"

        values = [h["value"] for h in history]
        avg = sum(values) / len(values)
        latest = values[0]
        oldest = values[-1]
        change = latest - oldest

        lines = [
            f"{label}趋势(最近{days}天, {len(history)}个数据点):",
            f"- 最新: {latest:.1f} ({history[0]['date']})",
            f"- 均值: {avg:.1f}",
            f"- 变化: {change:+.1f} (从{oldest:.1f}到{latest:.1f})",
            f"- 最大: {max(values):.1f}, 最小: {min(values):.1f}",
        ]

        if len(history) > 5:
            lines.append(
                "- 近期: "
                + ", ".join(f"{h['date'][-5:]}={h['value']:.1f}" for h in history[:7]),
            )

        if len(history) >= 3:
            dates_parsed = [
                datetime.strptime(h["date"], "%Y-%m-%d").date() for h in history
            ]
            gaps = []
            for i in range(len(dates_parsed) - 1):
                gap_days = (dates_parsed[i] - dates_parsed[i + 1]).days
                if gap_days > 3:
                    gaps.append(
                        f"{dates_parsed[i + 1]}~{dates_parsed[i]}缺{gap_days - 1}天",
                    )
            if gaps:
                lines.append(f"- 断档: {'; '.join(gaps[:3])}")

        return "\n".join(lines)

    async def _run_weekly(self, metric: str, weeks: int) -> str:
        label = WEEKLY_METRIC_LABELS.get(metric, metric)
        service = await self._get_accessor().get_service()
        weeks = min(weeks, 52)

        summaries = await service.get_weekly_summaries(limit=weeks)
        if not summaries:
            return f"最近{weeks}周无周汇总数据"

        values = []
        dates = []
        for s in summaries:
            val = getattr(s, metric, None)
            if val is not None:
                values.append(float(val))
                dates.append(str(s.week_start))

        if not values:
            return f"最近{weeks}周无{label}数据"

        avg = sum(values) / len(values)
        latest = values[0]
        oldest = values[-1]
        change = latest - oldest

        lines = [
            f"{label}周趋势(最近{weeks}周, {len(values)}个数据点):",
            f"- 最新: {latest:.1f} (周{dates[0]})",
            f"- 均值: {avg:.1f}",
            f"- 变化: {change:+.1f} (从{oldest:.1f}到{latest:.1f})",
            f"- 最大: {max(values):.1f}, 最小: {min(values):.1f}",
        ]

        if len(values) > 3:
            parts = [
                f"{d[-5:]}={v:.1f}" for d, v in zip(dates[:6], values[:6], strict=False)
            ]
            lines.append("- 近期: " + ", ".join(parts))

        return "\n".join(lines)


__all__ = ["QueryMetricTrendTool"]
