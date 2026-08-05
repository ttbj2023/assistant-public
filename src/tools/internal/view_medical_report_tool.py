"""健康体检报告工具 - view_medical_report.

查询最新体检报告详情与历史趋势.
"""

from __future__ import annotations

import logging
from typing import Any, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict

from src.tools.internal.health_data_helpers import HealthDataServiceAccessor
from src.tools.shared.tool_runtime import format_tool_error, sync_runnable

logger = logging.getLogger(__name__)


class ViewMedicalReportRequest(BaseModel):
    """健康体检报告请求模型."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )


@sync_runnable
class ViewMedicalReportTool(BaseTool):
    """健康体检报告."""

    name: str = "view_medical_report"
    description: str = "查询最新体检报告详情(类型/指标项)与历史报告数量. 无需参数."
    args_schema: type[ViewMedicalReportRequest] = ViewMedicalReportRequest

    def _get_accessor(self) -> HealthDataServiceAccessor:
        if not hasattr(self, "_health_acc"):
            acc = HealthDataServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_health_acc", acc)
        return self._health_acc

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            service = await self._get_accessor().get_service()
            result = await service.get_report_detail()

            if result.get("status") == "no_data":
                return "暂无体检报告"

            latest = result.get("latest", {})
            report_date = latest.get("report_date", "?")
            report_type = latest.get("report_type", "")
            data = latest.get("data", {})

            lines = [f"=== 体检报告 ({report_date}) ==="]
            if report_type:
                lines.append(f"类型: {report_type}")

            if data:
                lines.append(f"\n报告数据({len(data)}项):")
                for key, value in data.items():
                    lines.append(f"- {key}: {value}")

            history = result.get("history", {})
            total = history.get("total_reports", 0)
            if total > 1:
                lines.append(f"\n历史报告: 共{total}份")

            return "\n".join(lines)
        except Exception as e:
            logger.error("健康体检报告查询失败: %s", e)
            return format_tool_error(e)


__all__ = ["ViewMedicalReportTool"]
