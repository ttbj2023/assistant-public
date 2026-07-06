"""Storage Service层测试共享Fixtures.

提供Service层单元测试的通用Mock对象和测试工具。
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.storage.models.simple_pinned_memory import (
    SimplePinnedMemory,
    SimplePinnedMemoryType,
)
from src.storage.models.todo import TodoItem, TodoPriority, TodoStatus


@pytest.fixture
def mock_todo_item():
    """创建标准的TodoItem对象用于测试."""
    return TodoItem(
        id=1,
        title="测试TODO任务",
        description="这是一个测试任务描述",
        user_id="test_user",
        thread_id="test_thread_id",
        status=TodoStatus.PENDING,
        priority=TodoPriority.MEDIUM,
        due_date=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_pinned_memory():
    """创建标准的置顶记忆对象用于测试."""
    return SimplePinnedMemory(
        id=1,
        user_id="test_user",
        thread_id="test_thread_id",
        memory_type=SimplePinnedMemoryType.BASIC_INFO,
        content="测试置顶记忆内容",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_session_factory():
    """模拟SQLAlchemy会话工厂 - 正确实现嵌套异步上下文管理器."""
    # 创建真实的session Mock
    session = AsyncMock()
    session.begin = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()

    # 创建session.begin()返回的事务Mock
    transaction = AsyncMock()

    # 创建支持嵌套异步上下文管理器的Mock
    factory = MagicMock()

    # factory()返回的对象需要支持：
    # 1. async with factory() as session
    # 2. async with session.begin() as transaction
    class AsyncSessionMock:
        def __init__(self, session, transaction):
            self._session = session
            self._transaction = transaction

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, *args):
            pass

        def begin(self):
            """返回事务Mock."""

            class TransactionMock:
                async def __aenter__(self):
                    return self._transaction

                async def __aexit__(self, *args):
                    pass

            return TransactionMock()

    factory.return_value = AsyncSessionMock(session, transaction)

    return factory


@pytest.fixture
def mock_todo_dao():
    """模拟TodoDAO."""
    dao = AsyncMock()
    dao.list_all = AsyncMock(return_value=[])
    dao.list_by_status = AsyncMock(return_value=[])
    dao.create_todo = AsyncMock(
        return_value=TodoItem(
            id=1,
            title="Created TODO",
            user_id="test_user",
            thread_id="test_thread_id",
            status=TodoStatus.PENDING,
            priority=TodoPriority.MEDIUM,
        )
    )
    dao.update_todo = AsyncMock(return_value=None)
    dao.get_todo_by_id = AsyncMock(return_value=None)
    dao.delete_todo = AsyncMock(return_value=True)
    return dao


@pytest.fixture
def mock_memory_dao():
    """模拟PinnedMemoryDAO."""
    dao = AsyncMock()
    dao.get_all_memories = AsyncMock(return_value=[])
    dao.upsert_memory = AsyncMock(
        return_value=SimplePinnedMemory(
            id=1,
            user_id="test_user",
            thread_id="test_thread_id",
            memory_type=SimplePinnedMemoryType.BASIC_INFO,
            content="Updated content",
        )
    )
    dao.get_memory_by_type = AsyncMock(return_value=None)
    dao.delete_memory = AsyncMock(return_value=True)
    return dao


@pytest.fixture
def mock_todo_formatter():
    """模拟TODO格式化器."""
    formatter = AsyncMock()
    formatter.format_todolist = AsyncMock(return_value="格式化的TODO列表")
    return formatter


@pytest.fixture
def mock_memory_formatter():
    """模拟记忆格式化器."""
    formatter = Mock()
    formatter.sanitize_pinned_memory_data = Mock(side_effect=lambda x: x)
    formatter.format_pinned_memory = AsyncMock(return_value="格式化的记忆")
    return formatter


@pytest.fixture
def create_multiple_todo_items():
    """创建多个TODO项的工厂函数."""

    def _create_items(count=3):
        items = []
        for i in range(count):
            items.append(
                TodoItem(
                    id=i + 1,
                    title=f"测试任务{i + 1}",
                    description=f"任务{i + 1}的描述",
                    user_id="test_user",
                    thread_id="test_thread_id",
                    status=TodoStatus.PENDING if i % 2 == 0 else TodoStatus.COMPLETED,
                    priority=TodoPriority.HIGH if i == 0 else TodoPriority.MEDIUM,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        return items

    return _create_items


@pytest.fixture
def create_multiple_pinned_memories():
    """创建多个置顶记忆的工厂函数."""

    def _create_memories():
        memories = [
            SimplePinnedMemory(
                id=1,
                user_id="test_user",
                thread_id="test_thread_id",
                memory_type=SimplePinnedMemoryType.BASIC_INFO,
                content="基本信息内容",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
            SimplePinnedMemory(
                id=2,
                user_id="test_user",
                thread_id="test_thread_id",
                memory_type=SimplePinnedMemoryType.PREFERENCES,
                content="偏好设置内容",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        ]
        return memories

    return _create_memories


@pytest.fixture
def test_user():
    """标准测试用户ID."""
    return "test_user"
