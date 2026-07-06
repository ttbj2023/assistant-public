"""用户要求记事本工具 (requirement_memory).

主对话模型通过此工具全文重写维护"用户对助手的非一次性要求".
- 与置顶记忆分库: 本工具记"长期要求", 置顶记忆记"用户身份/口味事实".
- 全文重写语义 (无 action 枚举, 降低小模型幻觉面); 限额 ≤10 行 / ≤500 字,
  超限拒绝以迫使模型保留最重要的要求.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from pydantic import Field

from src.tools.shared.base_internal_tool import BaseInternalTool
from src.tools.shared.query_alias_model import QueryAliasModel

logger = logging.getLogger(__name__)


class RequirementMemoryInput(QueryAliasModel):
    """用户要求记事本输入 (query 作为 content 的隐藏别名, 不暴露进 schema)."""

    _field_aliases: ClassVar[dict[str, str]] = {"query": "content"}

    content: str = Field(
        default="",
        description=(
            "完整的用户要求列表, 一行一条; 空串=清空. 调用即全文覆盖当前记事本"
        ),
    )


class AsyncRequirementMemoryTool(BaseInternalTool):
    """用户要求记事本工具.

    主模型全文重写维护用户对助手的非一次性要求, 内容每轮注入 system prompt.
    """

    name: str = "requirement_memory"
    summary: str = "记录/更新用户对助手的非一次性要求"
    description: str = (
        "记录用户对助手的非一次性要求(记事本, 每轮注入系统提示词).\n"
        "用途: 保存用户明确提出的,关于你该如何运作/响应的长期要求"
        "(如'回复简洁''始终用英文''代码必须加注释').\n"
        "重要: 仅当用户明确提出要求时调用; 不要主动臆造或随意更新"
        "(除非用户要求修改/删除).\n"
        "\n"
        "边界 - 不要用本工具记录:\n"
        "- 一次性任务(如'帮我写报告')\n"
        "- 关于用户的事实(如'我叫张三''喜欢科幻') -> 不需要记, 系统会自动处理\n"
        "\n"
        "调用即全文覆盖: 提交完整的要求列表(一行一条, 最多10行/总共500字内). "
        "你在系统提示词的 <user_requirements> 里能看到当前内容, 更新时把"
        "当前列表加上你的改动一起提交. 空串=清空全部."
    )
    args_schema: type = RequirementMemoryInput
    search_keywords: ClassVar[list[str]] = [
        "要求",
        "偏好",
        "记录",
        "记事本",
        "requirement",
    ]

    def __init__(self, user_id: str, thread_id: str, **kwargs: Any) -> None:
        super().__init__(user_id, thread_id, **kwargs)
        self._service = None

    async def _get_service(self) -> Any:
        if self._service is not None:
            return self._service
        from src.storage.service import create_user_requirement_service

        self._service = await create_user_requirement_service(
            self.user_id,
            self.thread_id,
            agent_id=self.agent_id,
        )
        return self._service

    @override
    async def _arun(self, **kwargs: Any) -> str:
        """全文重写用户要求记事本."""
        try:
            content = (kwargs.get("content") or "").strip()
            service = await self._get_service()
            await service.set_content(self.user_id, self.thread_id, content)
            display = content if content else "(已清空)"
            return self._format_success(
                {
                    "current_requirements": display,
                    "line_count": len(content.splitlines()),
                },
                "已更新用户要求记事本",
            )
        except ValueError as e:
            # 限额校验失败: 把限额信息回传, 让模型重新精简
            return self._format_error(e, context="限额校验")
        except Exception as e:
            logger.error("requirement_memory 工具执行失败: %s", e)
            return self._format_error(e)


__all__ = ["AsyncRequirementMemoryTool", "RequirementMemoryInput"]
