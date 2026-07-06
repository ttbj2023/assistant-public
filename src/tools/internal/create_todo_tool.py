"""创建TODO任务工具 - create_todo."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.todo_manager_base import TodoManagerBase

logger = logging.getLogger(__name__)


class CreateTodoRequest(BaseModel):
    """创建任务请求."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    title: str = Field(..., description="任务标题(必填)")
    description: str | None = Field(None, description="任务描述")
    priority: str | None = Field(None, description="优先级: low/medium/high/urgent")
    status: str | None = Field(
        None,
        description="初始状态: pending/in_progress/completed/cancelled",
    )
    due_date: str | None = Field(None, description="截止日期, ISO格式如 2025-06-15")


class CreateTodoTool(TodoManagerBase):
    """创建一条TODO任务."""

    name: str = "create_todo"
    search_keywords: ClassVar[list[str]] = ["新建", "创建", "添加", "新增"]
    description: str = (
        "创建一条TODO任务.\n"
        "当用户要新建/添加任务时使用, 必须提供title; 其他字段仅在用户明确提及时才传入.\n\n"
        "示例:\n"
        '- 用户: "把买牛奶加入待办" → {"title": "买牛奶"}\n'
        '- 用户: "周五前交报告, 标高优先级" → {"title": "周五前交报告", "priority": "high", "due_date": "2025-07-03"}'
    )
    args_schema: type[CreateTodoRequest] = CreateTodoRequest

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = CreateTodoRequest(**kwargs)
            title = request.title.strip()
            if not title:
                return self._json_result(False, "任务标题不能为空")

            priority = self._parse_priority(request.priority)
            status = self._parse_status(request.status)
            due_date = self._parse_due_date(request.due_date)

            service = await self._get_todo_service()
            todo = await service.create_todo(
                title=title,
                user_id=self.user_id,
                thread_id=self.thread_id,
                description=request.description or "",
                priority=priority,
                status=status,
                due_date=due_date,
            )
            self._invalidate_todo_cache()
            todo_dict = self._todo_to_dict(todo)
            snapshot = await self._get_fresh_todolist()
            extra: dict[str, Any] = {"todo": todo_dict}
            if snapshot:
                extra["current_todos"] = snapshot
            return self._json_result(True, f"成功创建任务: {todo.title}", **extra)
        except Exception as e:
            logger.error("创建任务失败: %s", e)
            return self._json_result(False, f"创建任务失败: {e!s}")


__all__ = ["CreateTodoTool"]
