"""健康快照工具 - view_health_snapshot.

返回用户最新日完整数据 + 7日均值 + 数据新鲜度.
数据源为 HealthDataService, 只读.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict

from src.tools.internal.health_data_helpers import (
    HealthDataServiceAccessor,
    format_brief_fields,
    user_today,
)
from src.tools.shared.tool_runtime import format_tool_error, sync_runnable

logger = logging.getLogger(__name__)


class ViewHealthSnapshotRequest(BaseModel):
    """健康快照请求模型."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )


@sync_runnable
class ViewHealthSnapshotTool(BaseTool):
    """健康快照."""

    name: str = "view_health_snapshot"
    description: str = (
        "健康快照: 返回最新日数据 + 7日均值 + 数据新鲜度 + 近期运动 + 最新体检报告. "
        "无需参数, 推荐首次查询健康数据时使用."
    )
    args_schema: type[ViewHealthSnapshotRequest] = ViewHealthSnapshotRequest

    def _get_accessor(self) -> HealthDataServiceAccessor:
        if not hasattr(self, "_health_acc"):
            acc = HealthDataServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_health_acc", acc)
        return self._health_acc

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            ViewHealthSnapshotRequest(**kwargs)
            service = await self._get_accessor().get_service()

            end_date = user_today(self.user_id)
            start_7d = end_date - timedelta(days=6)

            summaries = await service.get_daily_summaries(start_7d, end_date)
            coverage = await service.get_data_coverage()
            latest_report = await service.get_latest_report()
            activity_summary = await service.get_weekly_activity_summary()

            lines = ["=== 健康快照 ==="]

            # 数据覆盖
            daily_meta = coverage.get("daily", {})
            total_days = daily_meta.get("total", 0)
            date_range = daily_meta.get("date_range", {})
            if total_days > 0:
                lines.append(
                    f"数据范围: {date_range.get('start', '?')} ~ {date_range.get('end', '?')} ({total_days}天)",
                )
            lines.append(f"近7天有数据: {len(summaries)}/7天")

            # 最新一天完整数据 (取最近有数据的一天)
            latest_s = summaries[0] if summaries else None
            if latest_s:
                lines.append(f"\n--- {latest_s.record_date} ---")
                lines.extend(format_brief_fields(latest_s))

            # 数据新鲜度
            if summaries:
                freshness = []
                for metric_key, label_short in [
                    ("body_mass_kg", "体重"),
                    ("resting_hr_bpm", "心率"),
                    ("sleep_duration_hours", "睡眠"),
                    ("steps", "步数"),
                ]:
                    last_day = next(
                        (
                            s.record_date
                            for s in summaries
                            if getattr(s, metric_key, None) is not None
                        ),
                        None,
                    )
                    if last_day:
                        days_ago = (end_date - last_day).days
                        if days_ago > 1:
                            freshness.append(f"{label_short}最新{days_ago}天前")
                if freshness:
                    lines.append(f"\n数据新鲜度: {', '.join(freshness)}")

            # 运动摘要
            if activity_summary.get("status") == "success":
                total = activity_summary.get("total_workouts", 0)
                dur = activity_summary.get("total_duration_minutes", 0)
                lines.append(f"\n近期运动: {total}次, 共{dur:.0f}分钟")

            # 体检报告
            if latest_report:
                lines.append(
                    f"体检报告: 最新{latest_report.report_date.strftime('%Y-%m-%d')}, {len(latest_report.report_data)}项",
                )

            return "\n".join(lines)
        except Exception as e:
            logger.error("健康快照查询失败: %s", e)
            return format_tool_error(e)


__all__ = ["ViewHealthSnapshotTool"]
