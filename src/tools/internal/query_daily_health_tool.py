"""健康每日明细工具 - query_daily_health.

查询指定单日或最近N天的每日健康明细.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.health_data_helpers import (
    HealthDataServiceAccessor,
    format_daily_brief,
    format_daily_detail,
    user_today,
)
from src.tools.shared.tool_runtime import format_tool_error, sync_runnable

logger = logging.getLogger(__name__)


class QueryDailyHealthRequest(BaseModel):
    """健康每日明细请求模型."""

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
        description="指定日期(YYYY-MM-DD), 查询单日明细",
    )


@sync_runnable
class QueryDailyHealthTool(BaseTool):
    """健康每日明细."""

    name: str = "query_daily_health"
    description: str = (
        "查询每日健康明细(活动/体征/睡眠/7日均值). "
        "参数: target_date 查单日; 不提供则查最近 days 天(默认7)."
    )
    args_schema: type[QueryDailyHealthRequest] = QueryDailyHealthRequest

    def _get_accessor(self) -> HealthDataServiceAccessor:
        if not hasattr(self, "_health_acc"):
            acc = HealthDataServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_health_acc", acc)
        return self._health_acc

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = QueryDailyHealthRequest(**kwargs)
            service = await self._get_accessor().get_service()

            if request.target_date:
                target = date.fromisoformat(request.target_date)
                summary = await service.get_daily_summary(target)
                if not summary:
                    return f"日期 {request.target_date} 无健康数据"
                return format_daily_detail(summary)

            days = min(request.days or 7, 90)
            end_date = user_today(self.user_id)
            start_date = end_date - timedelta(days=days - 1)
            summaries = await service.get_daily_summaries(start_date, end_date)

            if not summaries:
                return f"最近{days}天无健康数据"

            lines = [f"每日明细(最近{days}天, {len(summaries)}天有数据):"]
            for s in summaries:
                lines.append(format_daily_brief(s))
            return "\n".join(lines)
        except Exception as e:
            logger.error("健康每日明细查询失败: %s", e)
            return format_tool_error(e)


__all__ = ["QueryDailyHealthTool"]
