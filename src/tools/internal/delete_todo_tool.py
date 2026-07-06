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
    search_keywords: ClassVar[list[str]] = ["删除", "取消", "移除"]
    description: str = (
        "删除一条TODO任务.\n"
        "当用户要删除/取消任务时使用, 必须提供todo_id.\n"
        "先根据用户提到的任务标题, 在 <current_todos> 或 list_todos 结果中匹配对应 ID.\n\n"
        "示例:\n"
        '- 用户: "删掉买牛奶的任务" → 匹配到"买牛奶"的 todo_id 后, {"todo_id": 2}\n'
        '- 用户: "取消周报任务" → 匹配到"周报"的 todo_id 后, {"todo_id": 7}'
    )
    args_schema: type[DeleteTodoRequest] = DeleteTodoRequest

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = DeleteTodoRequest(**kwargs)
            service = await self._get_todo_service()
            deleted = await service.delete_todo(request.todo_id, self.user_id)

            if deleted:
                self._invalidate_todo_cache()
                snapshot = await self._get_fresh_todolist()
                extra: dict[str, Any] = {}
                if snapshot:
                    extra["current_todos"] = snapshot
                return self._json_result(
                    True, f"成功删除任务ID: {request.todo_id}", **extra
                )
            return self._json_result(
                False, f"任务ID {request.todo_id} 不存在或删除失败"
            )
        except Exception as e:
            logger.error("删除任务失败: %s", e)
            return self._json_result(False, f"删除任务失败: {e!s}")


__all__ = ["DeleteTodoTool"]
