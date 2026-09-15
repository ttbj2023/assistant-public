"""TODO 双向同步纯函数 (字段换算 + diff 计划).

无 IO: 引擎负责取数与执行, 本模块只做确定性换算与计划生成.
冲突策略: lastModifiedTime 新者胜 (相等本地胜); 本地 cancelled 视为
远端删除 (见 compute_todo_sync_plan).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.storage.models.todo import TodoItem, TodoPriority, TodoStatus

# 本地 status -> Graph status
_STATUS_TO_GRAPH = {
    TodoStatus.PENDING: "notStarted",
    TodoStatus.IN_PROGRESS: "inProgress",
    TodoStatus.COMPLETED: "completed",
}

# Graph status -> 本地 status
_STATUS_FROM_GRAPH = {
    "notStarted": TodoStatus.PENDING,
    "inProgress": TodoStatus.IN_PROGRESS,
    "completed": TodoStatus.COMPLETED,
}

# 本地 priority -> Graph importance (URGENT/HIGH 均映射 high, 有损)
_IMPORTANCE_TO_GRAPH = {
    TodoPriority.URGENT: "high",
    TodoPriority.HIGH: "high",
    TodoPriority.MEDIUM: "normal",
    TodoPriority.LOW: "low",
}

# Graph importance -> 本地 priority (high -> URGENT, 与 push 对称)
_IMPORTANCE_FROM_GRAPH = {
    "high": TodoPriority.URGENT,
    "normal": TodoPriority.MEDIUM,
    "low": TodoPriority.LOW,
}


@dataclass(frozen=True)
class SyncAction:
    """一条同步动作 (push = 本地→远端, pull = 远端→本地)."""

    kind: str  # push_create|push_update|push_delete|pull_create|pull_update|pull_delete
    local_id: int | None = None
    remote_id: str | None = None
    payload: dict[str, Any] | None = None  # Graph 请求体 (push_*)
    fields: dict[str, Any] | None = None  # 本地写入字段 (pull_*)


@dataclass(frozen=True)
class TodoSyncPlan:
    """一轮同步的动作集合."""

    actions: list[SyncAction] = field(default_factory=list)


def parse_graph_datetime(value: str | None) -> datetime | None:
    """解析 Graph 时间串 (Z 后缀 / 7 位小数秒) 为 aware UTC.

    Graph 返回形如 2026-09-15T08:00:00.0000000Z, 标准库 fromisoformat
    不接受 7 位小数, 需截断到 6 位.
    """
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    # 截断超过 6 位的小数秒
    if "." in normalized:
        head, _, tail = normalized.partition(".")
        sign_offset = tail.find("+")
        offset = tail[sign_offset:] if sign_offset >= 0 else ""
        digits = tail[:sign_offset] if sign_offset >= 0 else tail
        digits = digits[:6]
        normalized = f"{head}.{digits}{offset}"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def local_todo_to_graph_payload(todo: TodoItem) -> dict[str, Any]:
    """TodoItem -> Graph task 请求体 (创建/更新共用).

    dueDateTime 取 UTC 日期部分 (date-only 语义, 与 homelab 通道一致);
    description/body 与 due 为空时省略键.
    """
    payload: dict[str, Any] = {
        "title": todo.title,
        "status": _STATUS_TO_GRAPH.get(todo.status, "notStarted"),
        "importance": _IMPORTANCE_TO_GRAPH.get(todo.priority, "normal"),
    }
    if todo.due_date is not None:
        due = todo.due_date
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        payload["dueDateTime"] = {
            "dateTime": f"{due.astimezone(UTC).date().isoformat()}T00:00:00",
            "timeZone": "UTC",
        }
    if todo.description:
        payload["body"] = {"contentType": "text", "content": todo.description}
    return payload


def graph_task_to_local_fields(task: dict[str, Any]) -> dict[str, Any]:
    """Graph task -> 本地 TodoItem 写入字段."""
    due = None
    due_raw = task.get("dueDateTime")
    if isinstance(due_raw, dict) and due_raw.get("dateTime"):
        due = parse_graph_datetime(f"{str(due_raw['dateTime']).replace('Z', '')}+00:00")
    body = task.get("body") or {}
    status_value = str(task.get("status", "notStarted"))
    priority_value = str(task.get("importance", "normal"))
    return {
        "title": str(task.get("title", "")),
        "status": _STATUS_FROM_GRAPH.get(status_value, TodoStatus.PENDING),
        "priority": _IMPORTANCE_FROM_GRAPH.get(
            priority_value,
            TodoPriority.MEDIUM,
        ),
        "description": str(body.get("content") or ""),
        "due_date": due,
    }


def todo_content_hash(todo: TodoItem) -> str:
    """业务字段摘要 (不含 id/时间戳), 用于"本地是否已改"判定."""
    due = todo.due_date
    if due is not None and due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    parts = "|".join([
        todo.title,
        todo.description or "",
        str(todo.status.value if hasattr(todo.status, "value") else todo.status),
        str(todo.priority.value if hasattr(todo.priority, "value") else todo.priority),
        due.astimezone(UTC).isoformat() if due is not None else "",
    ])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def compute_todo_sync_plan(
    local_todos: list[TodoItem],
    remote_tasks: list[dict[str, Any]],
    mappings: list[Any],
) -> TodoSyncPlan:
    """计算一轮双向同步计划.

    Args:
        local_todos: 本地全部 todo (含各状态)
        remote_tasks: 专用清单内全部 Graph task
        mappings: 已有 SyncMap 映射

    Returns:
        动作集合; 空动作 = 双方一致

    """
    local_by_id = {t.id: t for t in local_todos if t.id is not None}
    remote_by_id = {str(t.get("id")): t for t in remote_tasks}
    actions: list[SyncAction] = []

    mapped_local_ids: set[int] = set()
    mapped_remote_ids: set[str] = set()

    for mapping in mappings:
        local = local_by_id.get(mapping.local_id)
        remote = remote_by_id.get(str(mapping.remote_id))
        mapped_local_ids.add(mapping.local_id)
        mapped_remote_ids.add(str(mapping.remote_id))

        if local is None:
            # 本地已删 (硬删除) -> 远端删除
            actions.append(
                SyncAction(kind="push_delete", remote_id=str(mapping.remote_id)),
            )
            continue
        if remote is None:
            # 远端已删 -> 本地删除
            actions.append(SyncAction(kind="pull_delete", local_id=mapping.local_id))
            continue
        if local.status == TodoStatus.CANCELLED:
            # 本地软取消 -> 远端删除
            actions.append(
                SyncAction(
                    kind="push_delete",
                    remote_id=str(mapping.remote_id),
                ),
            )
            continue

        local_hash = todo_content_hash(local)
        local_changed = mapping.content_hash != local_hash

        remote_modified = parse_graph_datetime(
            str(remote.get("lastModifiedDateTime", "")),
        )
        last_synced = mapping.last_synced_at
        if last_synced is not None and last_synced.tzinfo is None:
            # SQLite 读出的 naive 时间视为 UTC (仓库统一约定)
            last_synced = last_synced.replace(tzinfo=UTC)
        remote_changed = (
            remote_modified is not None
            and last_synced is not None
            and remote_modified > last_synced
        )

        if local_changed and not remote_changed:
            actions.append(
                SyncAction(
                    kind="push_update",
                    local_id=mapping.local_id,
                    remote_id=str(mapping.remote_id),
                    payload=local_todo_to_graph_payload(local),
                ),
            )
        elif remote_changed and not local_changed:
            actions.append(
                SyncAction(
                    kind="pull_update",
                    local_id=mapping.local_id,
                    remote_id=str(mapping.remote_id),
                    fields=graph_task_to_local_fields(remote),
                ),
            )
        elif local_changed and remote_changed:
            # 双方都改: 更新时间新者胜, 相等本地胜
            local_updated = local.updated_at
            if local_updated is not None and local_updated.tzinfo is None:
                local_updated = local_updated.replace(tzinfo=UTC)
            local_newer = (
                local_updated is not None
                and remote_modified is not None
                and local_updated >= remote_modified
            )
            if local_newer:
                actions.append(
                    SyncAction(
                        kind="push_update",
                        local_id=mapping.local_id,
                        remote_id=str(mapping.remote_id),
                        payload=local_todo_to_graph_payload(local),
                    ),
                )
            else:
                actions.append(
                    SyncAction(
                        kind="pull_update",
                        local_id=mapping.local_id,
                        remote_id=str(mapping.remote_id),
                        fields=graph_task_to_local_fields(remote),
                    ),
                )

    for todo in local_todos:
        if todo.id is None or todo.id in mapped_local_ids:
            continue
        # 未映射的已完成/已取消任务不同步 (避免灌入历史完成项)
        if todo.status in (TodoStatus.COMPLETED, TodoStatus.CANCELLED):
            continue
        actions.append(
            SyncAction(
                kind="push_create",
                local_id=todo.id,
                payload=local_todo_to_graph_payload(todo),
            ),
        )

    for remote_id, task in remote_by_id.items():
        if remote_id in mapped_remote_ids:
            continue
        actions.append(
            SyncAction(
                kind="pull_create",
                remote_id=remote_id,
                fields=graph_task_to_local_fields(task),
            ),
        )

    return TodoSyncPlan(actions=actions)


__all__ = [
    "SyncAction",
    "TodoSyncPlan",
    "compute_todo_sync_plan",
    "graph_task_to_local_fields",
    "local_todo_to_graph_payload",
    "parse_graph_datetime",
    "todo_content_hash",
]
