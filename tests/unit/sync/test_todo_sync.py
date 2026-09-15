"""todo_sync 纯函数测试 (转换 + 双向同步计划).

不依赖数据库/HTTP: 本地侧用 TodoItem 实例, 远端侧用 Graph task dict,
映射侧用 SyncMap 实例; 只验证 diff 逻辑与字段换算.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.storage.models.sync_map import SyncItemKind, SyncMap
from src.storage.models.todo import TodoItem, TodoPriority, TodoStatus
from src.sync.todo_sync import (
    compute_todo_sync_plan,
    graph_task_to_local_fields,
    local_todo_to_graph_payload,
    todo_content_hash,
)


def _local(
    *,
    id: int = 1,
    title: str = "买猫粮",
    status: TodoStatus = TodoStatus.PENDING,
    priority: TodoPriority = TodoPriority.MEDIUM,
    due: datetime | None = None,
    description: str | None = None,
    updated_at: datetime | None = None,
) -> TodoItem:
    return TodoItem(
        id=id,
        title=title,
        description=description,
        status=status,
        priority=priority,
        due_date=due,
        user_id="test_user",
        thread_id="t1",
        updated_at=updated_at or datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
    )


def _remote(
    *,
    rid: str = "rid-1",
    title: str = "买猫粮",
    status: str = "notStarted",
    last_modified: str = "2026-09-15T08:00:00.0000000Z",
    due: dict | None = None,
    importance: str = "normal",
    body: str | None = None,
) -> dict:
    task: dict = {
        "id": rid,
        "title": title,
        "status": status,
        "importance": importance,
        "lastModifiedDateTime": last_modified,
    }
    if due is not None:
        task["dueDateTime"] = due
    if body is not None:
        task["body"] = {"contentType": "text", "content": body}
    return task


def _mapping(
    *,
    local_id: int = 1,
    remote_id: str = "rid-1",
    content_hash: str | None = None,
    last_synced_at: datetime | None = None,
) -> SyncMap:
    return SyncMap(
        id=local_id,
        user_id="test_user",
        kind=SyncItemKind.TODO,
        local_id=local_id,
        remote_id=remote_id,
        content_hash=content_hash,
        last_synced_at=last_synced_at or datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
    )


class TestConverters:
    """字段换算测试."""

    def test_local转graph_payload_全字段(self):
        todo = _local(
            status=TodoStatus.IN_PROGRESS,
            priority=TodoPriority.URGENT,
            due=datetime(2026, 9, 20, 0, 0, tzinfo=UTC),
            description="备注",
        )

        payload = local_todo_to_graph_payload(todo)

        assert payload["title"] == "买猫粮"
        assert payload["status"] == "inProgress"
        assert payload["importance"] == "high"
        assert payload["dueDateTime"] == {
            "dateTime": "2026-09-20T00:00:00",
            "timeZone": "UTC",
        }
        assert payload["body"] == {"contentType": "text", "content": "备注"}

    def test_local转graph_payload_空字段省略(self):
        payload = local_todo_to_graph_payload(_local())

        assert "dueDateTime" not in payload
        assert "body" not in payload
        assert payload["status"] == "notStarted"
        assert payload["importance"] == "normal"

    def test_local转graph_payload_completed状态映射(self):
        payload = local_todo_to_graph_payload(_local(status=TodoStatus.COMPLETED))

        assert payload["status"] == "completed"

    def test_graph转local字段_全字段(self):
        task = _remote(
            status="completed",
            importance="low",
            due={"dateTime": "2026-09-20T00:00:00", "timeZone": "UTC"},
            body="备注",
        )

        fields = graph_task_to_local_fields(task)

        assert fields["title"] == "买猫粮"
        assert fields["status"] == TodoStatus.COMPLETED
        assert fields["priority"] == TodoPriority.LOW
        assert fields["description"] == "备注"
        assert fields["due_date"] == datetime(2026, 9, 20, 0, 0, tzinfo=UTC)

    def test_graph转local字段_缺省回退(self):
        fields = graph_task_to_local_fields(_remote())

        assert fields["status"] == TodoStatus.PENDING
        assert fields["priority"] == TodoPriority.MEDIUM
        assert fields["description"] == ""
        assert fields["due_date"] is None


class TestContentHash:
    """todo_content_hash 测试."""

    def test_相同内容_hash一致(self):
        assert todo_content_hash(_local()) == todo_content_hash(_local())

    def test_标题变化_hash不同(self):
        assert todo_content_hash(_local()) != todo_content_hash(
            _local(title="买狗粮"),
        )

    def test_naive与aware同刻_hash一致(self):
        # SQLite 读出的 naive updated_at 不参与 hash (只哈希业务字段)
        a = _local()
        b = _local()
        b.updated_at = datetime(2026, 9, 16, 12, 0)  # naive
        assert todo_content_hash(a) == todo_content_hash(b)


class TestSyncPlan:
    """compute_todo_sync_plan 测试."""

    def test_未映射pending任务_计划push_create(self):
        todo = _local(id=5)

        plan = compute_todo_sync_plan([todo], [], [])

        assert len(plan.actions) == 1
        action = plan.actions[0]
        assert action.kind == "push_create"
        assert action.local_id == 5
        assert action.payload["title"] == "买猫粮"

    def test_未映射completed任务_无动作(self):
        plan = compute_todo_sync_plan(
            [_local(status=TodoStatus.COMPLETED)],
            [],
            [],
        )

        assert plan.actions == []

    def test_映射存在本地已删_计划push_delete(self):
        plan = compute_todo_sync_plan([], [_remote()], [_mapping()])

        assert len(plan.actions) == 1
        assert plan.actions[0].kind == "push_delete"
        assert plan.actions[0].remote_id == "rid-1"

    def test_本地软取消_计划push_delete(self):
        todo = _local(status=TodoStatus.CANCELLED)
        mapping = _mapping(content_hash=todo_content_hash(_local()))

        plan = compute_todo_sync_plan([todo], [_remote()], [mapping])

        assert [a.kind for a in plan.actions] == ["push_delete"]

    def test_映射存在远端已删_计划pull_delete(self):
        plan = compute_todo_sync_plan([_local()], [], [_mapping()])

        assert len(plan.actions) == 1
        assert plan.actions[0].kind == "pull_delete"
        assert plan.actions[0].local_id == 1

    def test_本地已改远端未动_计划push_update(self):
        todo = _local(title="买猫粮(改)")
        mapping = _mapping(content_hash=todo_content_hash(_local()))

        plan = compute_todo_sync_plan([todo], [_remote()], [mapping])

        assert [a.kind for a in plan.actions] == ["push_update"]
        assert plan.actions[0].payload["title"] == "买猫粮(改)"

    def test_远端已改本地未动_计划pull_update(self):
        todo = _local()
        mapping = _mapping(content_hash=todo_content_hash(todo))
        remote = _remote(
            title="买猫粮(远端改)",
            last_modified="2026-09-15T09:00:00.0000000Z",
        )

        plan = compute_todo_sync_plan([todo], [remote], [mapping])

        assert [a.kind for a in plan.actions] == ["pull_update"]
        assert plan.actions[0].fields["title"] == "买猫粮(远端改)"

    def test_双方都改_本地更新时间新_计划push_update(self):
        todo = _local(
            title="本地新", updated_at=datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
        )
        mapping = _mapping(content_hash="stale")
        remote = _remote(
            title="远端旧",
            last_modified="2026-09-15T09:00:00.0000000Z",
        )

        plan = compute_todo_sync_plan([todo], [remote], [mapping])

        assert [a.kind for a in plan.actions] == ["push_update"]

    def test_双方都改_远端更新时间新_计划pull_update(self):
        todo = _local(title="本地旧")
        mapping = _mapping(content_hash="stale")
        remote = _remote(
            title="远端新",
            last_modified="2026-09-16T20:00:00.0000000Z",
        )

        plan = compute_todo_sync_plan([todo], [remote], [mapping])

        assert [a.kind for a in plan.actions] == ["pull_update"]

    def test_双方无变化_无动作(self):
        todo = _local()
        mapping = _mapping(content_hash=todo_content_hash(todo))

        plan = compute_todo_sync_plan([todo], [_remote()], [mapping])

        assert plan.actions == []

    def test_远端未映射新任务_计划pull_create(self):
        plan = compute_todo_sync_plan([], [_remote(rid="rid-new")], [])

        assert len(plan.actions) == 1
        assert plan.actions[0].kind == "pull_create"
        assert plan.actions[0].fields["title"] == "买猫粮"
