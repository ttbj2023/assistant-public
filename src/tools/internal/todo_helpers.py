"""TODO 工具共享逻辑 - 组合替代继承.

纯函数: parse_priority / parse_status / parse_due_date / todo_to_dict / json_result
Service 访问器: TodoServiceAccessor (带缓存)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from src.core.context import get_user_context_or_none
from src.core.datetime_utils import to_user_tz
from src.storage.models.todo import TodoPriority, TodoStatus

logger = logging.getLogger(__name__)


def parse_priority(priority_str: str | None) -> TodoPriority:
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


def parse_status(status_str: str | None) -> TodoStatus:
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


def parse_due_date(due_date_str: str | None) -> datetime | None:
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


def todo_to_dict(todo: Any) -> dict[str, Any]:
    """将 Todo 对象转换为字典.

    若 UserContext 已设置, 时间戳换算到用户时区; 否则原样输出.
    """
    ctx = get_user_context_or_none()

    def _iso(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        if ctx is not None:
            return to_user_tz(dt, ctx.timezone).isoformat()
        return dt.isoformat()

    return {
        "id": todo.id,
        "title": todo.title,
        "description": todo.description,
        "status": todo.status.value,
        "priority": todo.priority.value,
        "due_date": _iso(todo.due_date),
        "tags": todo.tags or "",
        "created_at": _iso(todo.created_at),
        "updated_at": _iso(todo.updated_at),
    }


def json_result(success: bool, message: str, **extra: Any) -> str:
    """构造统一的 JSON 结果字符串."""
    result = {"success": success, "message": message, **extra}
    return json.dumps(result, ensure_ascii=False)


class TodoServiceAccessor:
    """TODO Service 访问器 (组合, 带缓存)."""

    def __init__(self, user_id: str, thread_id: str, agent_id: str) -> None:
        self._user_id = user_id
        self._thread_id = thread_id
        self._agent_id = agent_id
        self._service: Any = None

    async def get_service(self) -> Any:
        """获取 TODO Service 实例 (带缓存)."""
        if self._service is not None:
            return self._service
        from src.storage.service import create_todo_service

        service = await create_todo_service(
            self._user_id,
            self._thread_id,
            agent_id=self._agent_id,
        )
        self._service = service
        return service

    async def get_fresh_todolist(self) -> list[dict[str, Any]]:
        """写操作后获取最新活跃任务快照 (硬保证降级)."""
        try:
            service = await self.get_service()
            todos = await service.list_todos(
                self._user_id,
                self._thread_id,
                statuses=[TodoStatus.PENDING, TodoStatus.IN_PROGRESS],
                limit=50,
            )
            return [todo_to_dict(t) for t in todos]
        except Exception as e:
            logger.warning("获取最新TODO列表失败(硬保证降级): %s", e)
            return []


__all__ = [
    "TodoServiceAccessor",
    "json_result",
    "parse_due_date",
    "parse_priority",
    "parse_status",
    "todo_to_dict",
]
