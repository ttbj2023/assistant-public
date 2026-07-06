"""TodoService单元测试.

测试TODO服务的业务逻辑，包括验证、格式化、CRUD操作等。
遵循单元测试设计规范：Mock外部依赖，测试业务逻辑。
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.storage.models.todo import TodoItem, TodoPriority, TodoStatus
from src.storage.service.todo_service import TodoService


class TestTodoServiceCreateTodo:
    """测试create_todo方法 - 核心业务逻辑."""

    @pytest.mark.asyncio
    async def test_create_todo_with_valid_data_should_succeed(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试创建TODO：有效数据应成功."""
        # Arrange
        mock_todo = TodoItem(
            id=1,
            title="测试TODO",
            user_id=test_user,
            thread_id="testthread_id",
            status=TodoStatus.PENDING,
            priority=TodoPriority.MEDIUM,
        )
        mock_todo_dao.create_todo = AsyncMock(return_value=mock_todo)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.create_todo(
            title="测试TODO",
            user_id=test_user,
            thread_id="testthread_id",
        )

        # Assert
        assert result is not None
        assert result.id == 1
        assert result.title == "测试TODO"
        mock_todo_dao.create_todo.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_todo_with_empty_title_should_raise_value_error(
        self, mock_session_factory, test_user
    ):
        """测试创建TODO：空标题应抛出ValueError."""
        # Arrange
        service = TodoService(mock_session_factory)

        # Act & Assert
        with pytest.raises(ValueError, match="TODO标题不能为空"):
            await service.create_todo(
                title="  ",
                user_id=test_user,
                thread_id="testthread_id",
            )

    @pytest.mark.asyncio
    async def test_create_todo_with_title_exceeding_200_chars_should_raise_value_error(
        self, mock_session_factory, test_user
    ):
        """测试创建TODO：标题超过200字符应抛出ValueError."""
        # Arrange
        service = TodoService(mock_session_factory)

        # Act & Assert
        with pytest.raises(ValueError, match="TODO标题长度不能超过200字符"):
            await service.create_todo(
                title="a" * 201,
                user_id=test_user,
                thread_id="testthread_id",
            )

    @pytest.mark.asyncio
    async def test_create_todo_with_title_exactly_200_chars_should_succeed(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试创建TODO：标题正好200字符应成功."""
        # Arrange
        mock_todo = TodoItem(
            id=1,
            title="a" * 200,
            user_id=test_user,
            thread_id="testthread_id",
            status=TodoStatus.PENDING,
            priority=TodoPriority.MEDIUM,
        )
        mock_todo_dao.create_todo = AsyncMock(return_value=mock_todo)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.create_todo(
            title="a" * 200,
            user_id=test_user,
            thread_id="testthread_id",
        )

        # Assert
        assert result is not None
        assert result.title == "a" * 200

    @pytest.mark.asyncio
    async def test_create_todo_with_description_exceeding_1000_chars_should_raise_value_error(
        self, mock_session_factory, test_user
    ):
        """测试创建TODO：描述超过1000字符应抛出ValueError."""
        # Arrange
        service = TodoService(mock_session_factory)

        # Act & Assert
        with pytest.raises(ValueError, match="TODO描述长度不能超过1000字符"):
            await service.create_todo(
                title="测试TODO",
                user_id=test_user,
                thread_id="testthread_id",
                description="a" * 1001,
            )

    @pytest.mark.asyncio
    async def test_create_todo_should_strip_whitespace_from_title_and_description(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试创建TODO：应去除标题和描述的首尾空白."""
        # Arrange
        saved_title = None
        saved_description = None

        async def mock_create(*args, **kwargs):
            nonlocal saved_title, saved_description
            saved_title = kwargs.get("title")
            saved_description = kwargs.get("description")
            return TodoItem(
                id=1,
                title=saved_title,
                description=saved_description,
                user_id=test_user,
                thread_id="testthread_id",
                status=TodoStatus.PENDING,
                priority=TodoPriority.MEDIUM,
            )

        mock_todo_dao.create_todo = AsyncMock(side_effect=mock_create)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        await service.create_todo(
            title="  测试TODO  ",
            user_id=test_user,
            thread_id="testthread_id",
            description="  测试描述  ",
        )

        # Assert
        assert saved_title == "测试TODO"
        assert saved_description == "测试描述"

    @pytest.mark.asyncio
    async def test_create_todo_should_set_default_status_to_pending(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试创建TODO：应设置默认状态为PENDING."""
        # Arrange
        saved_status = None

        async def mock_create(*args, **kwargs):
            nonlocal saved_status
            saved_status = kwargs.get("status")
            return TodoItem(
                id=1,
                title="测试TODO",
                user_id=test_user,
                thread_id="testthread_id",
                status=saved_status,
                priority=TodoPriority.MEDIUM,
            )

        mock_todo_dao.create_todo = AsyncMock(side_effect=mock_create)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        await service.create_todo(
            title="测试TODO",
            user_id=test_user,
            thread_id="testthread_id",
        )

        # Assert
        assert saved_status == TodoStatus.PENDING


class TestTodoServiceUpdateTodo:
    """测试update_todo方法."""

    @pytest.mark.asyncio
    async def test_update_todo_with_empty_title_should_raise_value_error(
        self, mock_session_factory, test_user
    ):
        """测试更新TODO：空标题应抛出ValueError."""
        # Arrange
        service = TodoService(mock_session_factory)

        # Act & Assert
        with pytest.raises(ValueError, match="TODO标题不能为空"):
            await service.update_todo(
                todo_id=1,
                user_id=test_user,
                title="  ",
            )

    @pytest.mark.asyncio
    async def test_update_todo_with_title_exceeding_200_chars_should_raise_value_error(
        self, mock_session_factory, test_user
    ):
        """测试更新TODO：标题超过200字符应抛出ValueError."""
        # Arrange
        service = TodoService(mock_session_factory)

        # Act & Assert
        with pytest.raises(ValueError, match="TODO标题长度不能超过200字符"):
            await service.update_todo(
                todo_id=1,
                user_id=test_user,
                title="a" * 201,
            )

    @pytest.mark.asyncio
    async def test_update_todo_with_description_exceeding_1000_chars_should_raise_value_error(
        self, mock_session_factory, test_user
    ):
        """测试更新TODO：描述超过1000字符应抛出ValueError."""
        # Arrange
        service = TodoService(mock_session_factory)

        # Act & Assert
        with pytest.raises(ValueError, match="TODO描述长度不能超过1000字符"):
            await service.update_todo(
                todo_id=1,
                user_id=test_user,
                description="a" * 1001,
            )


class TestTodoServiceListTodos:
    """测试list_todos方法."""

    @pytest.mark.asyncio
    async def test_list_todos_all_should_succeed(
        self, mock_todo_dao, mock_session_factory, test_user, create_multiple_todo_items
    ):
        """测试列出TODO：获取所有应成功."""
        # Arrange
        todos = create_multiple_todo_items(3)
        mock_todo_dao.list_by_filters = AsyncMock(return_value=todos)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.list_todos(user_id=test_user, thread_id="test_thread_id")

        # Assert
        assert len(result) == 3
        mock_todo_dao.list_by_filters.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_todos_by_status_should_filter(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试列出TODO：按状态过滤应成功."""
        # Arrange
        pending_todos = [
            TodoItem(
                id=i,
                title=f"Pending TODO {i}",
                user_id=test_user,
                thread_id="testthread_id",
                status=TodoStatus.PENDING,
                priority=TodoPriority.MEDIUM,
            )
            for i in range(1, 3)
        ]
        mock_todo_dao.list_by_filters = AsyncMock(return_value=pending_todos)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.list_todos(
            user_id=test_user,
            thread_id="testthread_id",
            status=TodoStatus.PENDING,
        )

        # Assert
        assert len(result) == 2
        assert all(todo.status == TodoStatus.PENDING for todo in result)
        mock_todo_dao.list_by_filters.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_todos_should_filter_by_user_and_thread(
        self, mock_todo_dao, mock_session_factory, create_multiple_todo_items
    ):
        """测试列出TODO：应按用户和线程过滤."""
        # Arrange
        todos = create_multiple_todo_items(5)
        mock_todo_dao.list_by_filters = AsyncMock(return_value=todos)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.list_todos(
            user_id="test_user", thread_id="testthread_id"
        )

        # Assert - list_by_filters 接收 user_id 和 thread_id 作为过滤条件
        assert len(result) == 5
        mock_todo_dao.list_by_filters.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_todos_should_respect_limit(
        self, mock_todo_dao, mock_session_factory, test_user, create_multiple_todo_items
    ):
        """测试列出TODO：应遵守限制."""
        # Arrange
        all_todos = create_multiple_todo_items(10)

        # Create a mock that respects the limit parameter
        async def mock_list_all(*args, **kwargs):
            limit = kwargs.get("limit", 100)
            return all_todos[:limit]

        mock_todo_dao.list_all = AsyncMock(side_effect=mock_list_all)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.list_todos(
            user_id=test_user, thread_id="testthread_id", limit=5
        )

        # Assert
        assert len(result) <= 5

    @pytest.mark.asyncio
    async def test_list_todos_with_statuses_should_pass_list_filter(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试列出TODO：statuses参数应传递列表到DAO过滤."""
        # Arrange
        active_todos = [
            TodoItem(
                id=1,
                title="待办任务",
                user_id=test_user,
                thread_id="testthread_id",
                status=TodoStatus.PENDING,
                priority=TodoPriority.MEDIUM,
            ),
            TodoItem(
                id=2,
                title="进行中任务",
                user_id=test_user,
                thread_id="testthread_id",
                status=TodoStatus.IN_PROGRESS,
                priority=TodoPriority.HIGH,
            ),
        ]
        mock_todo_dao.list_by_filters = AsyncMock(return_value=active_todos)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.list_todos(
            user_id=test_user,
            thread_id="testthread_id",
            statuses=[TodoStatus.PENDING, TodoStatus.IN_PROGRESS],
        )

        # Assert
        assert len(result) == 2
        call_kwargs = mock_todo_dao.list_by_filters.call_args
        # 验证传给DAO的filters包含列表形式的status
        assert "status" in call_kwargs[1]
        assert call_kwargs[1]["status"] == [TodoStatus.PENDING, TodoStatus.IN_PROGRESS]

    @pytest.mark.asyncio
    async def test_list_todos_statuses_should_take_precedence_over_status(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试列出TODO：statuses应优先于status参数."""
        # Arrange
        mock_todo_dao.list_by_filters = AsyncMock(return_value=[])
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        await service.list_todos(
            user_id=test_user,
            thread_id="testthread_id",
            status=TodoStatus.PENDING,
            statuses=[TodoStatus.PENDING, TodoStatus.IN_PROGRESS],
        )

        # Assert - statuses 应覆盖 status
        call_kwargs = mock_todo_dao.list_by_filters.call_args
        assert call_kwargs[1]["status"] == [TodoStatus.PENDING, TodoStatus.IN_PROGRESS]


class TestTodoServiceFormatTodos:
    """测试format_todos方法."""

    @pytest.mark.asyncio
    async def test_format_todos_with_provided_list_should_succeed(
        self,
        mock_todo_formatter,
        mock_session_factory,
        test_user,
        create_multiple_todo_items,
    ):
        """测试格式化TODO：提供列表应成功."""
        # Arrange
        todos = create_multiple_todo_items(3)
        service = TodoService(mock_session_factory)
        service.todo_formatter = mock_todo_formatter

        # Act
        result = await service.format_todos(
            todos=todos,
            user_id=test_user,
            thread_id="testthread_id",
        )

        # Assert
        assert result == "格式化的TODO列表"
        mock_todo_formatter.format_todolist.assert_called_once()

    @pytest.mark.asyncio
    async def test_format_todos_without_user_id_should_raise_runtime_error(
        self, mock_session_factory
    ):
        """测试格式化TODO：无user_id应抛出RuntimeError."""
        # Arrange
        service = TodoService(mock_session_factory)

        # Act & Assert
        with pytest.raises(RuntimeError, match="格式化TODO列表失败"):
            await service.format_todos(todos=None, user_id="")

    @pytest.mark.asyncio
    async def test_format_todos_should_convert_todo_items_to_dicts(
        self,
        mock_todo_formatter,
        mock_session_factory,
        test_user,
        create_multiple_todo_items,
    ):
        """测试格式化TODO：应转换TodoItem为字典."""
        # Arrange
        todos = create_multiple_todo_items(2)
        service = TodoService(mock_session_factory)
        service.todo_formatter = mock_todo_formatter

        # Act
        await service.format_todos(
            todos=todos,
            user_id=test_user,
            thread_id="testthread_id",
        )

        # Assert
        call_args = mock_todo_formatter.format_todolist.call_args[0][0]
        assert isinstance(call_args, list)
        assert all(isinstance(item, dict) for item in call_args)

    @pytest.mark.asyncio
    async def test_format_todos_should_handle_dict_input(
        self, mock_todo_formatter, mock_session_factory, test_user
    ):
        """测试格式化TODO：应处理字典输入."""
        # Arrange
        todo_dicts = [
            {
                "id": 1,
                "title": "TODO 1",
                "status": "PENDING",
                "priority": "MEDIUM",
            }
        ]
        service = TodoService(mock_session_factory)
        service.todo_formatter = mock_todo_formatter

        # Act
        await service.format_todos(
            todos=todo_dicts,
            user_id=test_user,
            thread_id="testthread_id",
        )

        # Assert
        mock_todo_formatter.format_todolist.assert_called_once()

    @pytest.mark.asyncio
    async def test_format_todos_should_fetch_from_db_when_todos_is_none(
        self,
        mock_todo_formatter,
        mock_session_factory,
        test_user,
        create_multiple_todo_items,
    ):
        """测试格式化TODO：todos为None时应从数据库获取."""
        # Arrange
        todos = create_multiple_todo_items(2)
        service = TodoService(mock_session_factory)
        service.todo_formatter = mock_todo_formatter

        # Mock list_todos
        service.list_todos = AsyncMock(return_value=todos)

        # Act
        await service.format_todos(
            todos=None,
            user_id=test_user,
            thread_id="testthread_id",
            status=TodoStatus.PENDING,
        )

        # Assert
        service.list_todos.assert_called_once_with(
            user_id=test_user,
            status=TodoStatus.PENDING,
            statuses=None,
            priority=None,
            limit=50,
        )


class TestTodoServiceGetFormattedTodolist:
    """测试get_formatted_todolist方法."""

    @pytest.mark.asyncio
    async def test_get_formatted_todolist_should_call_list_and_format(
        self, mock_session_factory, test_user
    ):
        """测试获取格式化TODO：应调用list和format."""
        # Arrange
        service = TodoService(mock_session_factory)

        # Mock both methods
        todos = [
            TodoItem(
                id=1,
                title="测试TODO",
                user_id=test_user,
                thread_id="testthread_id",
                status=TodoStatus.PENDING,
                priority=TodoPriority.MEDIUM,
            )
        ]
        service.list_todos = AsyncMock(return_value=todos)
        service.format_todos = AsyncMock(return_value="** TODO列表 **")

        # Act
        result = await service.get_formatted_todolist(
            user_id=test_user,
            thread_id="testthread_id",
            status=TodoStatus.PENDING,
        )

        # Assert
        assert result == "** TODO列表 **"
        service.list_todos.assert_called_once()
        service.format_todos.assert_called_once()


class TestTodoServiceHealthCheck:
    """测试健康检查相关方法."""

    def test_build_statistics_should_create_dict(self, mock_session_factory):
        """测试构建统计：应创建字典."""
        # Arrange
        service = TodoService(mock_session_factory)

        # Act
        result = service._build_statistics(
            total_todos=10,
            pending_todos=5,
            completed_todos=3,
            overdue_todos=2,
            due_today_todos=1,
            by_priority={"HIGH": 2, "MEDIUM": 5, "LOW": 3},
            latest_todo_time=datetime.now(UTC).isoformat(),
        )

        # Assert
        assert "total_todos" in result
        assert result["total_todos"] == 10
        assert "pending_todos" in result
        assert result["pending_todos"] == 5


class TestTodoServiceDeleteTodo:
    """测试delete_todo方法."""

    @pytest.mark.asyncio
    async def test_delete_todo_should_return_false_when_not_found(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试删除TODO: 不存在时应返回False."""
        # Arrange
        mock_todo_dao.get_todo_by_id = AsyncMock(return_value=None)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.delete_todo(todo_id=999, user_id=test_user)

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_todo_should_return_false_when_wrong_user(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试删除TODO: 不属于当前用户时应返回False."""
        # Arrange
        other_user_todo = TodoItem(
            id=1,
            title="别人的TODO",
            user_id="other_user",
            thread_id="other_thread",
            status=TodoStatus.PENDING,
            priority=TodoPriority.MEDIUM,
        )
        mock_todo_dao.get_todo_by_id = AsyncMock(return_value=other_user_todo)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.delete_todo(todo_id=1, user_id=test_user)

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_todo_should_succeed(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试删除TODO: 存在时应成功."""
        # Arrange
        todo = TodoItem(
            id=1,
            title="测试TODO",
            user_id=test_user,
            thread_id="test_thread",
            status=TodoStatus.PENDING,
            priority=TodoPriority.MEDIUM,
        )
        mock_todo_dao.get_todo_by_id = AsyncMock(return_value=todo)
        mock_todo_dao.delete_todo = AsyncMock(return_value=True)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.delete_todo(todo_id=1, user_id=test_user)

        # Assert
        assert result is True
        mock_todo_dao.delete_todo.assert_called_once_with(1)


class TestTodoServiceGetById:
    """测试get_todo_by_id方法."""

    @pytest.mark.asyncio
    async def test_get_todo_by_id_should_raise_when_not_found(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试获取TODO: 不存在时应抛出FileNotFoundError."""
        # Arrange
        mock_todo_dao.get_todo_by_id = AsyncMock(return_value=None)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act & Assert
        with pytest.raises(FileNotFoundError, match="不存在或无权限"):
            await service.get_todo_by_id(todo_id=999, user_id=test_user)

    @pytest.mark.asyncio
    async def test_get_todo_by_id_should_raise_when_wrong_user(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试获取TODO: 不属于当前用户时应抛出FileNotFoundError."""
        # Arrange
        other_user_todo = TodoItem(
            id=1,
            title="别人的TODO",
            user_id="other_user",
            thread_id="other_thread",
            status=TodoStatus.PENDING,
            priority=TodoPriority.MEDIUM,
        )
        mock_todo_dao.get_todo_by_id = AsyncMock(return_value=other_user_todo)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act & Assert
        with pytest.raises(FileNotFoundError, match="不存在或无权限"):
            await service.get_todo_by_id(todo_id=1, user_id=test_user)

    @pytest.mark.asyncio
    async def test_get_todo_by_id_should_return_todo(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试获取TODO: 存在时应返回TODO."""
        # Arrange
        todo = TodoItem(
            id=1,
            title="测试TODO",
            user_id=test_user,
            thread_id="test_thread",
            status=TodoStatus.PENDING,
            priority=TodoPriority.MEDIUM,
        )
        mock_todo_dao.get_todo_by_id = AsyncMock(return_value=todo)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.get_todo_by_id(todo_id=1, user_id=test_user)

        # Assert
        assert result.id == 1
        assert result.title == "测试TODO"


class TestTodoServiceUpdateTodoEdgeCases:
    """测试update_todo方法边缘情况."""

    @pytest.mark.asyncio
    async def test_update_todo_should_return_existing_when_no_fields(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试更新TODO: 无更新字段时应返回现有TODO."""
        # Arrange
        todo = TodoItem(
            id=1,
            title="原TODO",
            user_id=test_user,
            thread_id="test_thread",
            status=TodoStatus.PENDING,
            priority=TodoPriority.MEDIUM,
        )
        mock_todo_dao.get_todo_by_id = AsyncMock(return_value=todo)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.update_todo(todo_id=1, user_id=test_user)

        # Assert
        assert result.title == "原TODO"
        mock_todo_dao.update_todo.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_todo_should_raise_when_not_found(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试更新TODO: 不存在且要更新字段时应抛出FileNotFoundError."""
        # Arrange
        mock_todo_dao.get_todo_by_id = AsyncMock(return_value=None)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act & Assert
        with pytest.raises(FileNotFoundError, match="不存在或无权限"):
            await service.update_todo(todo_id=999, user_id=test_user, title="新标题")


class TestTodoServiceListTodosPrioritySorting:
    """测试list_todos的优先级排序."""

    @pytest.mark.asyncio
    async def test_list_todos_should_sort_by_priority_desc(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试列出TODO: 应按优先级降序排列."""
        # Arrange - DAO返回乱序的TODO
        todos = [
            TodoItem(
                id=i, title=f"TODO {i}", user_id=test_user,
                thread_id="test_thread",
                status=TodoStatus.PENDING,
                priority=p,
            )
            for i, p in enumerate([TodoPriority.LOW, TodoPriority.HIGH, TodoPriority.MEDIUM, TodoPriority.URGENT])
        ]
        mock_todo_dao.list_by_filters = AsyncMock(return_value=todos)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.list_todos(user_id=test_user, thread_id="test_thread")

        # Assert
        assert result[0].priority == TodoPriority.URGENT
        assert result[1].priority == TodoPriority.HIGH
        assert result[2].priority == TodoPriority.MEDIUM
        assert result[3].priority == TodoPriority.LOW


class TestTodoServiceHealthCheckFull:
    """测试完整的健康检查流程."""

    @pytest.mark.asyncio
    async def test_check_health_should_return_healthy(self, mock_session_factory):
        """测试健康检查: 正常时应返回healthy."""
        # Arrange - 需要mock session.execute
        mock_execute_result = AsyncMock()
        mock_execute_result.scalar.return_value = 0
        mock_execute_result.fetchall.return_value = []

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        factory = MagicMock()
        factory.return_value = mock_session
        factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        factory.return_value.__aexit__ = AsyncMock()

        class AsyncSessionMock:
            async def __aenter__(self):
                return mock_session

            async def __aexit__(self, *args):
                pass

        factory.return_value = AsyncSessionMock()
        service = TodoService(factory)

        # Act
        result = await service.health_check()

        # Assert
        assert result["status"] == "healthy"
        assert result["database_connected"] is True

    @pytest.mark.asyncio
    async def test_check_health_should_return_unhealthy_on_connection_error(
        self, mock_session_factory
    ):
        """测试健康检查: 连接错误时应返回unhealthy."""
        # Arrange
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("connection error")
        )
        factory.return_value.__aexit__ = AsyncMock()

        class AsyncSessionMock:
            async def __aenter__(self):
                raise Exception("connection error")

            async def __aexit__(self, *args):
                pass

        factory.return_value = AsyncSessionMock()
        service = TodoService(factory)

        # Act
        result = await service.health_check()

        # Assert
        assert result["status"] == "unhealthy"
        assert result["database_connected"] is False

    @pytest.mark.asyncio
    async def test_get_todo_statistics_should_return_empty_on_exception(
        self, mock_session_factory
    ):
        """测试统计: 异常时应返回空统计."""
        # Arrange
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("DB error")
        )
        factory.return_value.__aexit__ = AsyncMock()

        class AsyncSessionMock:
            async def __aenter__(self):
                raise Exception("DB error")

            async def __aexit__(self, *args):
                pass

        factory.return_value = AsyncSessionMock()
        service = TodoService(factory)

        # Act
        result = await service._get_todo_statistics()

        # Assert
        assert result["total_todos"] == 0
        assert result["pending_todos"] == 0


class TestTodoServiceListTodosPriority:
    """测试list_todos方法的priority过滤路径(覆盖line 99)."""

    @pytest.mark.asyncio
    async def test_list_todos_with_priority_filter_should_pass_to_dao(
        self, mock_todo_dao, mock_session_factory, test_user, create_multiple_todo_items
    ):
        """测试列出TODO：priority参数应传递到DAO过滤."""
        # Arrange
        todos = create_multiple_todo_items(3)
        mock_todo_dao.list_by_filters = AsyncMock(return_value=todos)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.list_todos(
            user_id=test_user, thread_id="test_thread",
            priority=TodoPriority.HIGH,
        )

        # Assert
        assert len(result) == 3
        call_kwargs = mock_todo_dao.list_by_filters.call_args
        assert call_kwargs[1].get("priority") == TodoPriority.HIGH


class TestTodoServiceCreateTodoExceptions:
    """测试create_todo方法的异常处理路径."""

    @pytest.mark.asyncio
    async def test_create_todo_with_dao_error_should_raise_runtime_error(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试创建TODO：DAO异常时应包装为RuntimeError."""
        # Arrange
        mock_todo_dao.create_todo = AsyncMock(side_effect=Exception("DB connection lost"))
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act & Assert
        with pytest.raises(RuntimeError, match="创建TODO失败"):
            await service.create_todo(
                title="测试TODO", user_id=test_user, thread_id="test_thread",
            )


class TestTodoServiceUpdateTodoFields:
    """测试update_todo方法的多字段更新和边缘路径."""

    @pytest.mark.asyncio
    async def test_update_todo_with_multiple_fields_should_pass_to_dao(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试更新TODO：同时更新描述、状态、优先级、截止日期应全部传递到DAO."""
        # Arrange
        from datetime import UTC, datetime, timedelta

        existing = TodoItem(
            id=1, title="原TODO", user_id=test_user, thread_id="test_thread",
            status=TodoStatus.PENDING, priority=TodoPriority.MEDIUM,
        )
        due_date = datetime.now(UTC) + timedelta(days=7)
        mock_todo_dao.get_todo_by_id = AsyncMock(return_value=existing)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao
        # Capture what update_data is passed to the DAO
        captured = {}

        async def mock_update(todo_id, **kwargs):
            captured.update(kwargs)
            return TodoItem(
                id=1, title="原TODO", user_id=test_user, thread_id="test_thread",
                description=kwargs.get("description", ""),
                status=kwargs.get("status", TodoStatus.PENDING),
                priority=kwargs.get("priority", TodoPriority.MEDIUM),
                due_date=kwargs.get("due_date"),
            )

        mock_todo_dao.update_todo = AsyncMock(side_effect=mock_update)

        # Act
        result = await service.update_todo(
            todo_id=1, user_id=test_user,
            description="新描述", status=TodoStatus.COMPLETED,
            priority=TodoPriority.HIGH, due_date=due_date,
        )

        # Assert
        assert result is not None
        assert captured.get("description") == "新描述"
        assert captured.get("status") == TodoStatus.COMPLETED
        assert captured.get("priority") == TodoPriority.HIGH
        assert captured.get("due_date") == due_date

    @pytest.mark.asyncio
    async def test_update_todo_no_fields_and_not_found_should_raise_error(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试更新TODO：无更新字段且TODO不存在时应抛出FileNotFoundError."""
        # Arrange
        mock_todo_dao.get_todo_by_id = AsyncMock(return_value=None)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act & Assert
        with pytest.raises(FileNotFoundError, match="不存在或无权限"):
            await service.update_todo(todo_id=999, user_id=test_user)

    @pytest.mark.asyncio
    async def test_update_todo_with_log_error_should_not_affect_result(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试更新TODO：日志记录DetachedInstanceError不应影响返回结果."""
        # Arrange
        existing = TodoItem(
            id=1, title="原TODO", user_id=test_user, thread_id="test_thread",
            status=TodoStatus.PENDING, priority=TodoPriority.MEDIUM,
        )
        updated = TodoItem(
            id=1, title="新标题", user_id=test_user, thread_id="test_thread",
            status=TodoStatus.PENDING, priority=TodoPriority.MEDIUM,
        )
        mock_todo_dao.get_todo_by_id = AsyncMock(return_value=existing)
        mock_todo_dao.update_todo = AsyncMock(return_value=updated)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao
        # 让 logger.info 在记录更新成功日志时抛出
        original_info = service.logger.info

        def _info_with_detached(*args, **kwargs):
            if "✅ TODO更新成功" in str(args[0]):
                raise Exception("DetachedInstanceError")
            return original_info(*args, **kwargs)

        service.logger.info = _info_with_detached

        # Act
        result = await service.update_todo(todo_id=1, user_id=test_user, title="新标题")

        # Assert - 日志异常不影响返回结果
        assert result is not None
        assert result.title == "新标题"

    @pytest.mark.asyncio
    async def test_update_todo_with_dao_error_should_raise_runtime_error(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试更新TODO：DAO异常时应包装为RuntimeError."""
        # Arrange
        existing = TodoItem(
            id=1, title="原TODO", user_id=test_user, thread_id="test_thread",
            status=TodoStatus.PENDING, priority=TodoPriority.MEDIUM,
        )
        mock_todo_dao.get_todo_by_id = AsyncMock(return_value=existing)
        mock_todo_dao.update_todo = AsyncMock(side_effect=Exception("DB update failed"))
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act & Assert
        with pytest.raises(RuntimeError, match="更新TODO失败"):
            await service.update_todo(todo_id=1, user_id=test_user, title="新标题")


class TestTodoServiceDeleteTodoExceptions:
    """测试delete_todo方法的异常处理路径."""

    @pytest.mark.asyncio
    async def test_delete_todo_with_dao_error_should_raise_runtime_error(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """测试删除TODO：DAO异常时应包装为RuntimeError."""
        # Arrange
        existing = TodoItem(
            id=1, title="测试TODO", user_id=test_user, thread_id="test_thread",
            status=TodoStatus.PENDING, priority=TodoPriority.MEDIUM,
        )
        mock_todo_dao.get_todo_by_id = AsyncMock(return_value=existing)
        mock_todo_dao.delete_todo = AsyncMock(side_effect=Exception("DB delete failed"))
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act & Assert
        with pytest.raises(RuntimeError, match="删除TODO失败"):
            await service.delete_todo(todo_id=1, user_id=test_user)


class TestTodoServiceGetFormattedTodoListExceptions:
    """测试get_formatted_todolist的异常处理."""

    @pytest.mark.asyncio
    async def test_get_formatted_todolist_with_error_should_raise_runtime_error(
        self, mock_session_factory, test_user
    ):
        """获取格式化TODO列表：内部异常时应包装为RuntimeError."""
        # Arrange
        service = TodoService(mock_session_factory)
        service.list_todos = AsyncMock(side_effect=Exception("list failed"))

        # Act & Assert
        with pytest.raises(RuntimeError, match="获取格式化TODO列表失败"):
            await service.get_formatted_todolist(
                user_id=test_user, thread_id="test_thread",
            )

    @pytest.mark.asyncio
    async def test_get_formatted_todolist_with_limit_should_pass_to_list_todos(
        self, mock_session_factory, test_user
    ):
        """获取格式化TODO列表：limit参数应传递到list_todos."""
        # Arrange
        service = TodoService(mock_session_factory)
        service.list_todos = AsyncMock(return_value=[])
        service.format_todos = AsyncMock(return_value="格式化列表")

        # Act
        await service.get_formatted_todolist(
            user_id=test_user, thread_id="test_thread", limit=20,
        )

        # Assert
        service.list_todos.assert_awaited_once()
        call_kwargs = service.list_todos.await_args
        assert "limit" in call_kwargs[1]
        assert call_kwargs[1]["limit"] == 20

    @pytest.mark.asyncio
    async def test_get_formatted_todolist_with_priority_should_pass_to_list_todos(
        self, mock_session_factory, test_user
    ):
        """获取格式化TODO列表：priority参数应传递到list_todos."""
        # Arrange
        service = TodoService(mock_session_factory)
        service.list_todos = AsyncMock(return_value=[])
        service.format_todos = AsyncMock(return_value="格式化列表")

        # Act
        await service.get_formatted_todolist(
            user_id=test_user, thread_id="test_thread",
            status=TodoStatus.PENDING, priority=TodoPriority.HIGH,
            include_section_title=True, format_template="html",
        )

        # Assert - priority 传入 list_todos; include_section_title/format_template 传入 format_todos
        call_kwargs = service.list_todos.await_args
        assert call_kwargs[1]["priority"] == TodoPriority.HIGH
        assert "format_template" not in call_kwargs[1]
        assert "include_section_title" not in call_kwargs[1]

    @pytest.mark.asyncio
    async def test_get_formatted_todolist_with_format_todos_error_should_raise_runtime_error(
        self, mock_session_factory, test_user
    ):
        """获取格式化TODO列表：format_todos异常时应包装为RuntimeError."""
        # Arrange
        service = TodoService(mock_session_factory)
        service.list_todos = AsyncMock(return_value=[])
        service.format_todos = AsyncMock(side_effect=Exception("format failed"))

        # Act & Assert
        with pytest.raises(RuntimeError, match="获取格式化TODO列表失败"):
            await service.get_formatted_todolist(
                user_id=test_user, thread_id="test_thread",
            )


class TestTodoServiceRemainingExceptions:
    """剩余未覆盖的异常路径."""

    @pytest.mark.asyncio
    async def test_list_todos_with_dao_error_should_raise_runtime_error(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """列出TODO：DAO异常时应包装为RuntimeError."""
        # Arrange
        mock_todo_dao.list_by_filters = AsyncMock(
            side_effect=Exception("DB error"),
        )
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act & Assert
        with pytest.raises(RuntimeError, match="获取TODO列表失败"):
            await service.list_todos(user_id=test_user, thread_id="test_thread")

    @pytest.mark.asyncio
    async def test_create_todo_without_id_should_log_warning(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """创建TODO：返回的TODO无ID应记录警告(覆盖line 211)."""
        # Arrange - DAO返回id=0(falsy)的TodoItem → 触发警告日志分支
        mock_no_id = TodoItem(
            id=0, title="测试TODO", user_id=test_user,
            thread_id="test_thread", status=TodoStatus.PENDING,
            priority=TodoPriority.MEDIUM,
        )
        mock_todo_dao.create_todo = AsyncMock(return_value=mock_no_id)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act
        result = await service.create_todo(
            title="测试TODO", user_id=test_user, thread_id="test_thread",
        )

        # Assert - 返回的id为0但业务上仍是有效TODO
        assert result is not None
        assert result.id == 0

    @pytest.mark.asyncio
    async def test_create_todo_with_post_log_error_should_not_raise(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """创建TODO：创建后日志记录异常不应影响返回结果."""
        # Arrange
        mock_todo = TodoItem(
            id=1, title="测试TODO", user_id=test_user,
            thread_id="test_thread", status=TodoStatus.PENDING,
            priority=TodoPriority.MEDIUM,
        )
        mock_todo_dao.create_todo = AsyncMock(return_value=mock_todo)
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao
        # 让 logger.info 在记录创建成功日志时抛出
        original_info = service.logger.info

        def _info_with_error(*args, **kwargs):
            if "TODO创建成功" in str(args[0]):
                raise Exception("DetachedInstanceError")
            return original_info(*args, **kwargs)

        service.logger.info = _info_with_error

        # Act
        result = await service.create_todo(
            title="测试TODO", user_id=test_user, thread_id="test_thread",
        )

        # Assert - 日志异常不影响返回结果
        assert result is not None
        assert result.id == 1

    @pytest.mark.asyncio
    async def test_get_todo_by_id_with_dao_error_should_raise_runtime_error(
        self, mock_todo_dao, mock_session_factory, test_user
    ):
        """获取TODO：DAO异常时应包装为RuntimeError."""
        # Arrange
        mock_todo_dao.get_todo_by_id = AsyncMock(
            side_effect=Exception("DB error"),
        )
        service = TodoService(mock_session_factory)
        service.todo_dao = mock_todo_dao

        # Act & Assert
        with pytest.raises(RuntimeError, match="获取TODO失败"):
            await service.get_todo_by_id(todo_id=1, user_id=test_user)


class TestTodoServiceGetTodoStatistics:
    """测试_get_todo_statistics完整数据路径."""

    @pytest.mark.asyncio
    async def test_get_todo_statistics_should_return_full_stats(
        self, mock_session_factory
    ):
        """测试统计：应返回包含latest_todo_time的完整统计."""
        # Arrange
        mock_execute_results = {}

        def make_scalar_mock(values):
            """Create a mock execute result that returns scalar values in order."""
            iterator = iter(values)

            class ScalarMock:
                async def scalar(self):
                    return next(iterator)

                def fetchall(self):
                    return []

            return ScalarMock()

        class PriorityMock:
            def __init__(self, data):
                self._data = data

            def scalar(self):
                return None

            def fetchall(self):
                return self._data

        # 用于统计的查询次数：6 次 execute 调用
        call_count = [0]

        class SessionWithStats:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def execute(self, stmt, params=None):
                idx = call_count[0]
                call_count[0] = idx + 1
                # 第1次: COUNT(*)
                if idx == 0:
                    result = MagicMock()
                    result.scalar.return_value = 10
                    return result
                # 第2次: pending
                if idx == 1:
                    result = MagicMock()
                    result.scalar.return_value = 4
                    return result
                # 第3次: completed
                if idx == 2:
                    result = MagicMock()
                    result.scalar.return_value = 3
                    return result
                # 第4次: overdue
                if idx == 3:
                    result = MagicMock()
                    result.scalar.return_value = 1
                    return result
                # 第5次: due today
                if idx == 4:
                    result = MagicMock()
                    result.scalar.return_value = 2
                    return result
                # 第6次: by priority
                if idx == 5:
                    result = MagicMock()
                    result.fetchall.return_value = [("HIGH", 2), ("MEDIUM", 5)]
                    return result
                # 第7次: latest_time(lines 711-716)
                if idx == 6:
                    from datetime import UTC, datetime
                    result = MagicMock()
                    result.scalar.return_value = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
                    return result

                result = MagicMock()
                result.scalar.return_value = None
                return result

        factory = MagicMock()
        factory.return_value = SessionWithStats()
        service = TodoService(factory)

        # Act
        result = await service._get_todo_statistics()

        # Assert - 验证所有统计字段
        assert result["total_todos"] == 10
        assert result["pending_todos"] == 4
        assert result["completed_todos"] == 3
        assert result["overdue_todos"] == 1
        assert result["due_today_todos"] == 2
        assert result["by_priority"] == {"HIGH": 2, "MEDIUM": 5}
        # latest_time.isoformat() → "2026-07-01T12:00:00+00:00"
        assert isinstance(result["latest_todo_time"], str) and "2026-07-01" in result["latest_todo_time"]
