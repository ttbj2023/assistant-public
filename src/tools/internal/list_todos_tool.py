"""查看TODO任务列表工具 - list_todos."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.storage.models.todo import TodoStatus
from src.tools.internal.todo_helpers import (
    TodoServiceAccessor,
    json_result,
    parse_priority,
    parse_status,
    todo_to_dict,
)
from src.tools.shared.tool_runtime import sync_runnable

logger = logging.getLogger(__name__)


class ListTodosRequest(BaseModel):
    """查看任务列表请求."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    status: str | None = Field(
        None, description="按状态过滤: pending/in_progress/completed/cancelled"
    )
    priority: str | None = Field(
        None, description="按优先级过滤: low/medium/high/urgent"
    )
    limit: int | None = Field(None, description="返回数量限制, 默认50")


@sync_runnable
class ListTodosTool(BaseTool):
    """查看TODO任务列表."""

    name: str = "list_todos"
    search_keywords: ClassVar[list[str]] = ["查看", "列出", "显示"]
    description: str = (
        "查看TODO任务列表.\n"
        "当用户要看任务/有哪些任务时使用.\n"
        "默认返回活跃任务(PENDING + IN_PROGRESS), 包含 [#N] ID 可供 update/delete 引用; 可按 status/priority 过滤.\n\n"
        "示例:\n"
        '- 用户: "我有哪些待办" → {}\n'
        '- 用户: "列出已完成的任务" → {"status": "completed"}'
    )
    args_schema: type[ListTodosRequest] = ListTodosRequest

    def _get_accessor(self) -> TodoServiceAccessor:
        if not hasattr(self, "_todo_acc"):
            acc = TodoServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_todo_acc", acc)
        return self._todo_acc

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = ListTodosRequest(**kwargs)
            provided = request.model_fields_set

            status = (
                parse_status(request.status)
                if "status" in provided and request.status
                else None
            )
            priority = (
                parse_priority(request.priority)
                if "priority" in provided and request.priority
                else None
            )
            limit = min(request.limit or 50, 100)

            service = await self._get_accessor().get_service()
            if status is not None:
                todos = await service.list_todos(
                    self.user_id,
                    self.thread_id,
                    status=status,
                    priority=priority,
                    limit=limit,
                )
            else:
                todos = await service.list_todos(
                    self.user_id,
                    self.thread_id,
                    statuses=[TodoStatus.PENDING, TodoStatus.IN_PROGRESS],
                    priority=priority,
                    limit=limit,
                )
            todo_dicts = [todo_to_dict(t) for t in todos]
            count = len(todo_dicts)
            message = f"共 {count} 条活跃任务" if count else "没有找到任务"
            return json_result(True, message, todos=todo_dicts, count=count)
        except Exception as e:
            logger.error("获取任务列表失败: %s", e)
            return json_result(
                False, f"获取任务列表失败: {e!s}", action=None, error=str(e)
            )


__all__ = ["ListTodosTool"]
