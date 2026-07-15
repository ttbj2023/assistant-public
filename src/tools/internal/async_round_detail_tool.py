"""对话轮次详情工具 - 按轮次号获取完整原文 (索引区下钻 fetch 工具).

填补索引区下钻缺口: 索引区 fine 行(round+topic+summary)和 arc 短语只给概览,
本工具按 round_number 精确取回完整 user_message + assistant_response.
与 search_memories(按 query 搜, 返回钩子) 互补: 一个按内容定位, 一个按轮次取原文.
"""

from __future__ import annotations

import logging
from typing import Any, override

from pydantic import BaseModel, ConfigDict, Field

from src.storage.service import create_conversation_service
from src.tools.shared.base_internal_tool import BaseInternalTool

logger = logging.getLogger(__name__)


class GetRoundDetailRequest(BaseModel):
    """轮次详情参数模型 (Strict模式兼容)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    round_numbers: list[int] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="要获取完整原文的轮次号列表, 来自索引区 fine 行或 search_memories 钩子",
    )


class AsyncRoundDetailTool(BaseInternalTool):
    """按轮次号获取对话完整原文."""

    name: str = "get_round_detail"
    description: str = """按轮次号获取对话的完整原文(fetch 语义).

索引区表格(Round 列)与 search_memories 返回的 [轮X] 钩子都含轮次号,
用本工具按 round_number 取回完整 user_message + assistant_response.

示例: {"round_numbers": [5, 12]}
"""
    args_schema: type[GetRoundDetailRequest] = GetRoundDetailRequest

    _conversation_service: Any

    def __init__(self, user_id: str, thread_id: str, **kwargs: Any) -> None:
        """初始化轮次详情工具."""
        if not user_id or not user_id.strip():
            raise ValueError("用户ID不能为空")

        if not thread_id or not thread_id.strip():
            raise ValueError("线程ID不能为空")

        super().__init__(user_id, thread_id, **kwargs)
        object.__setattr__(self, "_conversation_service", None)

    async def _get_service(self) -> Any:
        """获取对话服务实例 (lazy-init + 缓存)."""
        if self._conversation_service is not None:
            return self._conversation_service

        service = await create_conversation_service(
            user_id=self.user_id,
            thread_id=self.thread_id,
            agent_id=self.agent_id,
        )
        object.__setattr__(self, "_conversation_service", service)
        logger.info(
            f"AsyncRoundDetailTool 初始化完成: {self.user_id}/{self.thread_id}",
        )
        return service

    @override
    async def _arun(self, round_numbers: list[int]) -> str:
        """按轮次号获取完整对话原文."""
        try:
            if not round_numbers:
                raise ValueError("round_numbers 不能为空")

            service = await self._get_service()
            conversations = await service.get_conversations_by_rounds(
                self.user_id,
                self.thread_id,
                round_numbers,
            )

            conv_by_round = {c.round_number: c for c in conversations}
            results = []
            for rn in round_numbers:
                conv = conv_by_round.get(rn)
                if conv is None:
                    continue
                content_parts = []
                user_msg = getattr(conv, "user_message", "")
                assistant_msg = getattr(conv, "assistant_response", "")
                if user_msg:
                    content_parts.append(f"用户: {user_msg}")
                if assistant_msg:
                    content_parts.append(f"助手: {assistant_msg}")
                results.append({
                    "round_number": rn,
                    "topic": getattr(conv, "topic", None),
                    "summary": getattr(conv, "summary", None),
                    "content": "\n\n".join(content_parts),
                })

            not_found = [rn for rn in round_numbers if rn not in conv_by_round]

            return self._format_success(
                {
                    "results": results,
                    "total_count": len(results),
                    "not_found": not_found,
                },
                message=f"获取 {len(results)} 轮对话详情",
            )

        except Exception as e:
            logger.error("❌ 获取轮次详情失败: %s", e)
            return self._format_error(e)


__all__ = ["AsyncRoundDetailTool", "GetRoundDetailRequest"]
