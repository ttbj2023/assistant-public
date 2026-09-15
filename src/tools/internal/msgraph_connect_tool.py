"""MSGraph 授权连接工具 - msgraph_connect.

引导用户完成 Outlook (Microsoft 个人账户) 的 device code 授权,
授权后 TODO 与日历自动同步到手机的 To Do / Outlook 应用.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict

from src.sync.graph_auth_service import get_graph_auth_service
from src.tools.internal.todo_helpers import json_result
from src.tools.shared.tool_runtime import sync_runnable

logger = logging.getLogger(__name__)


class MsGraphConnectRequest(BaseModel):
    """连接请求 (无需参数)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )


@sync_runnable
class MsGraphConnectTool(BaseTool):
    """发起或查看 Outlook 同步授权 (device code flow)."""

    name: str = "msgraph_connect"
    search_keywords: ClassVar[list[str]] = [
        "Outlook",
        "微软",
        "同步",
        "授权",
        "To Do",
        "绑定",
    ]
    description: str = (
        "把用户的 Microsoft 个人账户 (Outlook) 与助手连接, "
        "连接后 TODO 与日程自动同步到手机原生 To Do / Outlook 应用.\n"
        "当用户提到 '同步到 Outlook' / '连一下微软账户' / 'To Do 同步' 时使用.\n"
        "发起后返回验证链接与用户码: 请把链接和码发给用户, 让用户在**任意设备的浏览器**"
        "打开链接、登录其本人的微软账户并输入该码, 全程不需要你的参与.\n"
        "用户完成后授权自动生效, 可用 msgraph_sync_status 查询结果."
    )
    args_schema: type[MsGraphConnectRequest] = MsGraphConnectRequest

    async def is_available(self) -> bool:
        """授权完成后隐藏 (认证是一次性任务, 已授权无需再发现此工具).

        休眠池与搜索 catalog 均按本门控过滤; 解绑后自动重新可见.
        """
        from src.sync.token_store import GraphTokenStore

        return not GraphTokenStore(self.user_id).exists()

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            service = get_graph_auth_service()
            status = service.get_status(self.user_id)

            if status.get("status") == "authorized":
                return json_result(
                    True,
                    "已授权, TODO 与日程正在自动同步; 如需换账户请先经 REST 解绑",
                    auth_status="authorized",
                )
            if status.get("status") == "pending":
                return json_result(
                    True,
                    "授权进行中, 等待用户在浏览器完成输码",
                    auth_status="pending",
                    verification_uri=status.get("verification_uri"),
                    user_code=status.get("user_code"),
                )

            flow = await service.start_authorization(
                self.user_id,
                thread_id=self.thread_id,
                agent_id=self.agent_id,
            )
            return json_result(
                True,
                "已发起授权, 请用户打开链接并输入用户码 (15 分钟内有效)",
                verification_uri=flow.get("verification_uri"),
                user_code=flow.get("user_code"),
                expires_in=flow.get("expires_in"),
            )
        except Exception as e:
            logger.error("msgraph_connect 失败: %s", e)
            return json_result(False, f"发起授权失败: {e!s}", error=str(e))


__all__ = ["MsGraphConnectTool"]
