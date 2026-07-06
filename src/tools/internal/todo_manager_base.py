"""TODO工具共享基类.

提供 create_todo / list_todos / update_todo / delete_todo 四个子工具的公共逻辑:
Service获取/优先级状态解析/截止日期解析/缓存失效.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from src.tools.shared.base_internal_tool import BaseInternalTool

from ...storage.models.todo import TodoPriority, TodoStatus

logger = logging.getLogger(__name__)


class TodoManagerBase(BaseInternalTool):
    """TODO工具共享基类."""

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        *,
        agent_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(user_id, thread_id, agent_id=agent_id, **kwargs)
        self._todo_service: Any = None

    async def _get_todo_service(self) -> Any:
        """获取TODO Service实例(带缓存)."""
        if self._todo_service is not None:
            return self._todo_service
        from ...storage.service import create_todo_service

        service = await create_todo_service(
            self.user_id,
            self.thread_id,
            agent_id=self.agent_id,
        )
        self._todo_service = service
        return service

    def _parse_priority(self, priority_str: str | None) -> TodoPriority:
        """解析优先级字符串, 无效值抛出 ValueError. 支持中英文."""
        if not priority_str:
            return TodoPriority.MEDIUM

        priority_map = {
            "low": TodoPriority.LOW,
            "medium": TodoPriority.MEDIUM,
            "high": TodoPriority.HIGH,
            "urgent": TodoPriority.URGENT,
            "低": TodoPriority.LOW,
            "中": TodoPriority.MEDIUM,
            "普通": TodoPriority.MEDIUM,
            "高": TodoPriority.HIGH,
            "紧急": TodoPriority.URGENT,
        }

        priority_lower = priority_str.strip().lower()
        result = priority_map.get(priority_lower)
        if result is None:
            valid = ", ".join(priority_map.keys())
            raise ValueError(f"无效的优先级 '{priority_str}', 有效值: {valid}")
        return result

    def _parse_status(self, status_str: str | None) -> TodoStatus:
        """解析状态字符串, 无效值抛出 ValueError. 支持中英文."""
        if not status_str:
            return TodoStatus.PENDING

        status_map = {
            "pending": TodoStatus.PENDING,
            "in_progress": TodoStatus.IN_PROGRESS,
            "completed": TodoStatus.COMPLETED,
            "cancelled": TodoStatus.CANCELLED,
            "待办": TodoStatus.PENDING,
            "待处理": TodoStatus.PENDING,
            "进行中": TodoStatus.IN_PROGRESS,
            "已完成": TodoStatus.COMPLETED,
            "已取消": TodoStatus.CANCELLED,
        }

        status_lower = status_str.strip().lower()
        result = status_map.get(status_lower)
        if result is None:
            valid = ", ".join(status_map.keys())
            raise ValueError(f"无效的状态 '{status_str}', 有效值: {valid}")
        return result

    def _parse_due_date(self, due_date_str: str | None) -> datetime | None:
        """解析截止日期字符串."""
        if not due_date_str:
            return None

        try:
            return datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "截止日期解析失败: '%s', 请使用ISO格式如'2024-12-13T10:00:00'",
                due_date_str,
            )
            return None

    def _todo_to_dict(self, todo: Any) -> dict[str, Any]:
        """将Todo对象转换为字典."""
        return {
            "id": todo.id,
            "title": todo.title,
            "description": todo.description,
            "status": todo.status.value,
            "priority": todo.priority.value,
            "due_date": todo.due_date.isoformat() if todo.due_date else None,
            "tags": todo.tags or "",
            "created_at": todo.created_at.isoformat() if todo.created_at else None,
            "updated_at": todo.updated_at.isoformat() if todo.updated_at else None,
        }

    def _invalidate_todo_cache(self) -> None:
        """写操作后清除TODO缓存, 确保下次记忆组装时重新从数据库加载."""
        from src.tools.shared.todo_cache_invalidator import invalidate_todo_cache

        invalidate_todo_cache(self.user_id, self.thread_id, agent_id=self.agent_id)
        logger.debug(f"已清除TODO缓存: {self.user_id}:{self.thread_id}")

    async def _get_fresh_todolist(self) -> str:
        """写操作后获取最新TODO列表快照(硬保证: 在工具返回中附上数据库真实状态).

        口径与记忆组装的 <current_todos> 一致, 只取 PENDING + IN_PROGRESS 活跃任务,
        避免模型凭轮初快照或猜测描述任务. 失败时降级返回空串, 不影响写操作的成功返回.
        """
        try:
            service = await self._get_todo_service()
            return await service.get_formatted_todolist(
                self.user_id,
                self.thread_id,
                statuses=[TodoStatus.PENDING, TodoStatus.IN_PROGRESS],
                limit=50,
                include_section_title=True,
                format_template="markdown",
            )
        except Exception as e:
            logger.warning("获取最新TODO列表失败(硬保证降级): %s", e)
            return ""

    @staticmethod
    def _json_result(success: bool, message: str, **extra: Any) -> str:
        """构造统一的JSON结果字符串(供 _summarize_tool_result 提取 message)."""
        result = {"success": success, "message": message, **extra}
        return json.dumps(result, ensure_ascii=False)


__all__ = ["TodoManagerBase"]
