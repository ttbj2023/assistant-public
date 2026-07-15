"""更新TODO任务工具 - update_todo."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.todo_manager_base import TodoManagerBase

logger = logging.getLogger(__name__)


class UpdateTodoRequest(BaseModel):
    """更新任务请求. 只更新显式提供的字段, 未提供的保持原值."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    todo_id: int = Field(..., description="要更新的任务ID(必填)")
    title: str | None = Field(None, description="新标题")
    description: str | None = Field(None, description="新描述")
    priority: str | None = Field(None, description="新优先级: low/medium/high/urgent")
    status: str | None = Field(
        None,
        description="新状态: pending/in_progress/completed/cancelled",
    )
    due_date: str | None = Field(None, description="新截止日期, ISO格式")


class UpdateTodoTool(TodoManagerBase):
    """更新一条TODO任务, 只更新提供的字段."""

    name: str = "update_todo"
    search_keywords: ClassVar[list[str]] = ["修改", "更新", "完成", "改状态"]
    description: str = (
        "更新一条TODO任务.\n"
        "当用户要修改/完成/改状态/取消时使用, 必须提供todo_id.\n"
        "先根据用户提到的任务标题, 在 list_todos 结果或写工具返回的 current_todos 中匹配对应 ID, 只更新用户明确要求的字段.\n"
        "注意: 将 status 设为 cancelled 表示软取消(保留记录); 若要彻底删除记录, 请使用 delete_todo.\n\n"
        "示例:\n"
        '- 用户: "把买牛奶的任务标记为已完成" → 匹配到"买牛奶"的 todo_id 后, {"todo_id": 3, "status": "completed"}\n'
        '- 用户: "把周报任务改成低优先级" → 匹配到"周报"的 todo_id 后, {"todo_id": 5, "priority": "low"}\n'
        '- 用户: "取消买牛奶的任务" → {"todo_id": 3, "status": "cancelled"} (保留记录, 非删除)'
    )
    args_schema: type[UpdateTodoRequest] = UpdateTodoRequest

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = UpdateTodoRequest(**kwargs)
            service = await self._get_todo_service()

            update_kwargs: dict[str, Any] = {
                "todo_id": request.todo_id,
                "user_id": self.user_id,
            }

            provided = request.model_fields_set

            if "title" in provided and request.title:
                update_kwargs["title"] = request.title.strip()

            if "description" in provided and request.description:
                update_kwargs["description"] = request.description.strip()

            if "priority" in provided and request.priority is not None:
                update_kwargs["priority"] = self._parse_priority(request.priority)

            if "status" in provided and request.status is not None:
                update_kwargs["status"] = self._parse_status(request.status)

            if "due_date" in provided and request.due_date is not None:
                update_kwargs["due_date"] = self._parse_due_date(request.due_date)

            updated = await service.update_todo(**update_kwargs)
            todo_dict = self._todo_to_dict(updated)
            current_todos = await self._get_fresh_todolist()
            extra: dict[str, Any] = {
                "action": "updated",
                "affected_todo_id": updated.id,
                "todo": todo_dict,
                "current_todos": current_todos,
            }
            return self._json_result(True, f"成功更新任务: {updated.title}", **extra)
        except ValueError as e:
            logger.error("更新任务失败(验证错误): %s", e)
            return self._json_result(False, str(e), action=None, error=str(e))
        except Exception as e:
            logger.error("更新任务失败: %s", e)
            return self._json_result(
                False, f"更新任务失败: {e!s}", action=None, error=str(e)
            )


__all__ = ["UpdateTodoTool"]
