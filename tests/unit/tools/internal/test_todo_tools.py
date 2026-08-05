"""TODO子工具单元测试.

覆盖拆分后的四个子工具:
- CreateTodoTool / ListTodosTool / UpdateTodoTool / DeleteTodoTool

以及 todo_helpers 共享逻辑.
Mock: create_todo_service.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.storage.models.todo import TodoPriority, TodoStatus
from src.tools.internal.create_todo_tool import CreateTodoTool
from src.tools.internal.delete_todo_tool import DeleteTodoTool
from src.tools.internal.list_todos_tool import ListTodosTool
from src.tools.internal.todo_helpers import (
    json_result,
    parse_due_date,
    parse_priority,
    parse_status,
    todo_to_dict,
)
from src.tools.internal.update_todo_tool import UpdateTodoTool
from src.tools.shared.tool_runtime import inject_identity


def _mock_acc(service):
    """创建 mock TodoServiceAccessor (真实对象, 预设 service 缓存)."""
    from src.tools.internal.todo_helpers import TodoServiceAccessor

    acc = TodoServiceAccessor.__new__(TodoServiceAccessor)
    acc._user_id = "u1"
    acc._thread_id = "t1"
    acc._agent_id = "a1"
    acc._service = service
    return acc


def _mock_acc_err(err):
    """创建出错时降级的 mock accessor."""
    acc = MagicMock()
    acc.get_service = AsyncMock(side_effect=err)
    acc.get_fresh_todolist = AsyncMock(return_value=[])
    return acc


@pytest.fixture
def create_tool():
    tool = CreateTodoTool()
    inject_identity(tool, "u1", "t1", "a1")
    return tool


@pytest.fixture
def list_tool():
    tool = ListTodosTool()
    inject_identity(tool, "u1", "t1", "a1")
    return tool


@pytest.fixture
def update_tool():
    tool = UpdateTodoTool()
    inject_identity(tool, "u1", "t1", "a1")
    return tool


@pytest.fixture
def delete_tool():
    tool = DeleteTodoTool()
    inject_identity(tool, "u1", "t1", "a1")
    return tool


def _make_todo(todo_id=1, title="测试任务"):
    todo = MagicMock()
    todo.id = todo_id
    todo.title = title
    todo.description = ""
    todo.status = MagicMock(value="pending")
    todo.priority = MagicMock(value="medium")
    todo.due_date = None
    todo.tags = ""
    todo.created_at = None
    todo.updated_at = None
    return todo


@pytest.fixture
def mock_service():
    svc = AsyncMock()
    svc.create_todo = AsyncMock(return_value=_make_todo())
    svc.list_todos = AsyncMock(return_value=[_make_todo()])
    svc.update_todo = AsyncMock(return_value=_make_todo())
    svc.delete_todo = AsyncMock(return_value=True)
    svc.get_formatted_todolist = AsyncMock(return_value="## 待办\n- [1] 测试任务")
    return svc


# ========== CreateTodoTool ==========


class TestCreateTodo:
    @pytest.mark.asyncio
    async def test_create_success(self, create_tool, mock_service):
        with (
            patch.object(create_tool, "_get_accessor", return_value=_mock_acc(mock_service)),

        ):
            result = await create_tool._arun(title="新任务")
        data = json.loads(result)
        assert data["success"] is True
        assert "成功创建任务" in data["message"]
        mock_service.create_todo.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_empty_title(self, create_tool):
        result = await create_tool._arun(title="  ")
        data = json.loads(result)
        assert data["success"] is False
        assert "标题不能为空" in data["message"]
        assert data["error"] == "任务标题不能为空"

    @pytest.mark.asyncio
    async def test_create_with_priority(self, create_tool, mock_service):
        with (
            patch.object(create_tool, "_get_accessor", return_value=_mock_acc(mock_service)),

        ):
            await create_tool._arun(title="任务", priority="high")
        assert (
            mock_service.create_todo.call_args.kwargs["priority"] == TodoPriority.HIGH
        )

    @pytest.mark.asyncio
    async def test_create_success_returns_structured(self, create_tool, mock_service):
        """创建成功后返回结构化结果: action/affected_todo_id/todo/current_todos."""
        with (
            patch.object(create_tool, "_get_accessor", return_value=_mock_acc(mock_service)),

        ):
            result = await create_tool._arun(title="新任务")
        data = json.loads(result)
        assert data["success"] is True
        assert data["action"] == "created"
        assert data["affected_todo_id"] == 1
        assert data["todo"]["id"] == 1
        assert data["todo"]["title"] == "测试任务"
        assert isinstance(data["current_todos"], list)
        assert len(data["current_todos"]) == 1
        assert data["current_todos"][0]["title"] == "测试任务"
        # 只取活跃任务
        call_kwargs = mock_service.list_todos.call_args.kwargs
        assert call_kwargs["statuses"] == [TodoStatus.PENDING, TodoStatus.IN_PROGRESS]

    @pytest.mark.asyncio
    async def test_create_attaches_empty_list_when_no_active(
        self, create_tool, mock_service
    ):
        """无活跃任务时 current_todos 为空列表, 保持形状稳定."""
        mock_service.list_todos = AsyncMock(return_value=[])
        with (
            patch.object(create_tool, "_get_accessor", return_value=_mock_acc(mock_service)),

        ):
            result = await create_tool._arun(title="新任务")
        data = json.loads(result)
        assert data["success"] is True
        assert data["current_todos"] == []


# ========== ListTodosTool ==========


class TestListTodos:
    @pytest.mark.asyncio
    async def test_list_success_returns_structured(self, list_tool, mock_service):
        """list_todos 成功返回 todos(list)/count/message."""
        with patch.object(list_tool, "_get_accessor", return_value=_mock_acc(mock_service)):
            result = await list_tool._arun()
        data = json.loads(result)
        assert data["success"] is True
        assert isinstance(data["todos"], list)
        assert len(data["todos"]) == 1
        assert data["todos"][0]["title"] == "测试任务"
        assert data["count"] == 1
        assert "共 1 条" in data["message"]

    @pytest.mark.asyncio
    async def test_list_empty(self, list_tool, mock_service):
        mock_service.list_todos = AsyncMock(return_value=[])
        with patch.object(list_tool, "_get_accessor", return_value=_mock_acc(mock_service)):
            result = await list_tool._arun()
        data = json.loads(result)
        assert data["success"] is True
        assert data["todos"] == []
        assert data["count"] == 0
        assert data["message"] == "没有找到任务"

    @pytest.mark.asyncio
    async def test_list_default_uses_active_statuses(self, list_tool, mock_service):
        """默认返回活跃任务(PENDING + IN_PROGRESS)."""
        with patch.object(list_tool, "_get_accessor", return_value=_mock_acc(mock_service)):
            await list_tool._arun()
        call_kwargs = mock_service.list_todos.call_args.kwargs
        assert call_kwargs["statuses"] == [TodoStatus.PENDING, TodoStatus.IN_PROGRESS]

    @pytest.mark.asyncio
    async def test_list_failure_returns_error(self, list_tool, mock_service):
        """列表查询异常时返回 error 字段(修复 fallback 丢错)."""
        mock_service.list_todos = AsyncMock(side_effect=RuntimeError("db down"))
        with patch.object(list_tool, "_get_accessor", return_value=_mock_acc(mock_service)):
            result = await list_tool._arun()
        data = json.loads(result)
        assert data["success"] is False
        assert "db down" in data["error"]


# ========== UpdateTodoTool ==========


class TestUpdateTodo:
    @pytest.mark.asyncio
    async def test_update_success(self, update_tool, mock_service):
        with (
            patch.object(update_tool, "_get_accessor", return_value=_mock_acc(mock_service)),
        ):
            result = await update_tool._arun(todo_id=1, status="completed")
        data = json.loads(result)
        assert data["success"] is True
        mock_service.update_todo.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_only_provided_fields(self, update_tool, mock_service):
        with (
            patch.object(update_tool, "_get_accessor", return_value=_mock_acc(mock_service)),
        ):
            await update_tool._arun(todo_id=1, title="新标题")
        kwargs = mock_service.update_todo.call_args.kwargs
        assert kwargs["title"] == "新标题"
        assert "status" not in kwargs

    @pytest.mark.asyncio
    async def test_update_failure_returns_error(self, update_tool):
        """无效输入时返回 error 字段(修复 fallback 丢错)."""
        result = await update_tool._arun(todo_id=1, status="invalid")
        data = json.loads(result)
        assert data["success"] is False
        assert "无效的状态" in data["error"]

    @pytest.mark.asyncio
    async def test_update_success_returns_structured(self, update_tool, mock_service):
        """更新成功后返回结构化结果: action/affected_todo_id/todo/current_todos."""
        with (
            patch.object(update_tool, "_get_accessor", return_value=_mock_acc(mock_service)),
        ):
            result = await update_tool._arun(todo_id=1, status="completed")
        data = json.loads(result)
        assert data["success"] is True
        assert data["action"] == "updated"
        assert data["affected_todo_id"] == 1
        assert data["todo"]["id"] == 1
        assert data["todo"]["status"] == "pending"
        assert isinstance(data["current_todos"], list)
        assert len(data["current_todos"]) == 1
        assert data["current_todos"][0]["title"] == "测试任务"


# ========== DeleteTodoTool ==========


class TestDeleteTodo:
    @pytest.mark.asyncio
    async def test_delete_success_returns_structured(self, delete_tool, mock_service):
        """删除成功后返回结构化结果: action/affected_todo_id/current_todos, 无 todo."""
        with (
            patch.object(delete_tool, "_get_accessor", return_value=_mock_acc(mock_service)),
        ):
            result = await delete_tool._arun(todo_id=1)
        data = json.loads(result)
        assert data["success"] is True
        assert data["action"] == "deleted"
        assert data["affected_todo_id"] == 1
        assert isinstance(data["current_todos"], list)
        assert len(data["current_todos"]) == 1
        assert data["current_todos"][0]["title"] == "测试任务"
        assert "todo" not in data
        mock_service.delete_todo.assert_called_once_with(1, "u1")

    @pytest.mark.asyncio
    async def test_delete_not_found(self, delete_tool, mock_service):
        mock_service.delete_todo = AsyncMock(return_value=False)
        with patch.object(delete_tool, "_get_accessor", return_value=_mock_acc(mock_service)):
            result = await delete_tool._arun(todo_id=999)
        data = json.loads(result)
        assert data["success"] is False
        assert data["error"] == "任务ID 999 不存在或删除失败"


# ========== todo_helpers 共享逻辑 ==========


class TestTodoHelpers:
    def test_parse_priority_default(self, create_tool):
        assert parse_priority(None) == TodoPriority.MEDIUM

    def test_parse_priority_chinese(self, create_tool):
        assert parse_priority("高") == TodoPriority.HIGH

    def test_parse_priority_invalid_raises(self, create_tool):
        with pytest.raises(ValueError, match="无效的优先级"):
            parse_priority("xxx")

    def test_parse_status_default(self, create_tool):
        assert parse_status(None) == TodoStatus.PENDING

    def test_parse_status_chinese(self, create_tool):
        assert parse_status("已完成") == TodoStatus.COMPLETED

    def test_parse_due_date_none(self, create_tool):
        assert parse_due_date(None) is None

    def test_parse_due_date_valid(self, create_tool):
        from datetime import datetime

        assert isinstance(parse_due_date("2025-06-15T10:00:00"), datetime)

    def test_json_result(self):
        data = json.loads(json_result(True, "ok", extra=1))
        assert data == {"success": True, "message": "ok", "extra": 1}


class TestTodoToDictTimezone:
    """todo_to_dict 在 UserContext 下应换算时间戳到用户时区."""

    @staticmethod
    def _make_todo(due_date):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=1,
            title="t",
            description=None,
            status=TodoStatus.PENDING,
            priority=TodoPriority.HIGH,
            due_date=due_date,
            tags=None,
            created_at=due_date,
            updated_at=due_date,
        )

    def test_converts_timestamps_to_user_tz(self):
        from src.core.context import (
            UserContext,
            reset_user_context,
            set_user_context,
        )

        # UTC 2024-01-15 02:00 -> America/New_York (UTC-5) = 2024-01-14 21:00
        todo = self._make_todo(datetime(2024, 1, 15, 2, 0, tzinfo=UTC))
        token = set_user_context(
            UserContext(
                user_id="u",
                thread_id="t",
                agent_id="a",
                timezone="America/New_York",
            ),
        )
        try:
            d = todo_to_dict(todo)
        finally:
            reset_user_context(token)
        assert "-05:00" in d["due_date"]
        assert d["due_date"].startswith("2024-01-14")
        assert "-05:00" in d["created_at"]

    def test_no_context_returns_naive_unchanged(self):
        # 无 context -> 原样 isoformat (naive 无后缀)
        todo = self._make_todo(datetime(2024, 1, 15, 2, 0))
        d = todo_to_dict(todo)
        assert d["due_date"] == "2024-01-15T02:00:00"
        assert d["created_at"] == "2024-01-15T02:00:00"

    @pytest.mark.asyncio
    async def test_get_fresh_todolist_degrades_on_error(self, create_tool):
        """_get_fresh_todolist 异常时降级返回空列表, 不影响写操作的成功返回."""
        with patch(
            "src.storage.service.create_todo_service",
            side_effect=RuntimeError("db down"),
        ):
            snapshot = await create_tool._get_accessor().get_fresh_todolist()
        assert snapshot == []

    @pytest.mark.asyncio
    async def test_get_fresh_todolist_returns_structured_list(
        self, create_tool, mock_service
    ):
        """_get_fresh_todolist 返回 list[dict] 且只取活跃任务."""
        with patch.object(create_tool, "_get_accessor", return_value=_mock_acc(mock_service)):
            snapshot = await create_tool._get_accessor().get_fresh_todolist()
        assert isinstance(snapshot, list)
        assert len(snapshot) == 1
        assert snapshot[0]["id"] == 1
        assert snapshot[0]["title"] == "测试任务"
        assert snapshot[0]["status"] == "pending"
        call_kwargs = mock_service.list_todos.call_args.kwargs
        assert call_kwargs["statuses"] == [TodoStatus.PENDING, TodoStatus.IN_PROGRESS]
