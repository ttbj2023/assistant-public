"""删除TODO任务工具 - delete_todo."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.todo_manager_base import TodoManagerBase

logger = logging.getLogger(__name__)


class DeleteTodoRequest(BaseModel):
    """删除任务请求."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    todo_id: int = Field(..., description="要删除的任务ID(必填)")


class DeleteTodoTool(TodoManagerBase):
    """删除一条TODO任务."""

    name: str = "delete_todo"
    search_keywords: ClassVar[list[str]] = ["删除", "移除", "彻底删除"]
    description: str = (
        "彻底删除一条TODO任务(不可恢复).\n"
        "当用户要删除/移除任务时使用, 必须提供todo_id.\n"
        "先根据用户提到的任务标题, 在 list_todos 结果或写工具返回的 current_todos 中匹配对应 ID.\n"
        '注意: 删除是物理删除, 记录不可恢复; 若只想标记为已取消(保留记录), 请使用 update_todo(status="cancelled").\n\n'
        "示例:\n"
        '- 用户: "删掉买牛奶的任务" → 匹配到"买牛奶"的 todo_id 后, {"todo_id": 2}\n'
        '- 用户: "移除周报任务" → 匹配到"周报"的 todo_id 后, {"todo_id": 7}'
    )
    args_schema: type[DeleteTodoRequest] = DeleteTodoRequest

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = DeleteTodoRequest(**kwargs)
            service = await self._get_todo_service()
            deleted = await service.delete_todo(request.todo_id, self.user_id)

            if deleted:
                current_todos = await self._get_fresh_todolist()
                extra: dict[str, Any] = {
                    "action": "deleted",
                    "affected_todo_id": request.todo_id,
                    "current_todos": current_todos,
                }
                return self._json_result(
                    True, f"成功删除任务ID: {request.todo_id}", **extra
                )
            return self._json_result(
                False,
                f"任务ID {request.todo_id} 不存在或删除失败",
                action=None,
                error=f"任务ID {request.todo_id} 不存在或删除失败",
            )
        except Exception as e:
            logger.error("删除任务失败: %s", e)
            return self._json_result(
                False, f"删除任务失败: {e!s}", action=None, error=str(e)
            )


__all__ = ["DeleteTodoTool"]
