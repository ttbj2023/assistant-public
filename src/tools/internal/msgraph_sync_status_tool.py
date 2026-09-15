"""MSGraph 同步状态查询工具 - msgraph_sync_status."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict

from src.sync.graph_auth_service import get_graph_auth_service
from src.sync.graph_sync_engine import get_graph_sync_engine
from src.tools.internal.todo_helpers import json_result
from src.tools.shared.tool_runtime import sync_runnable

logger = logging.getLogger(__name__)


class MsGraphSyncStatusRequest(BaseModel):
    """状态查询请求 (无需参数)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )


@sync_runnable
class MsGraphSyncStatusTool(BaseTool):
    """查询 Outlook 同步的授权状态与引擎运行状态."""

    name: str = "msgraph_sync_status"
    search_keywords: ClassVar[list[str]] = [
        "同步状态",
        "同步了吗",
        "Outlook 状态",
        "授权状态",
    ]
    description: str = (
        "查询 Outlook (Microsoft Graph) 同步状态: 授权状态 (none/pending/"
        "failed/authorized) 与同步引擎最近一轮的执行结果.\n"
        "当用户问 '同步成功了吗' / 'Outlook 连接状态' / 授权后确认结果时使用.\n"
        "授权 pending 附验证链接与用户码; failed 附失败原因."
    )
    args_schema: type[MsGraphSyncStatusRequest] = MsGraphSyncStatusRequest

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            status = get_graph_auth_service().get_status(self.user_id)
            engine = get_graph_sync_engine()
            return json_result(
                True,
                "同步状态查询成功",
                auth_status=status.get("status"),
                verification_uri=status.get("verification_uri"),
                user_code=status.get("user_code"),
                error=status.get("error"),
                engine_running=engine.stats.running,
                last_sync_outcome=engine.stats.user_outcomes.get(self.user_id),
            )
        except Exception as e:
            logger.error("msgraph_sync_status 失败: %s", e)
            return json_result(False, f"查询同步状态失败: {e!s}", error=str(e))


__all__ = ["MsGraphSyncStatusTool"]
