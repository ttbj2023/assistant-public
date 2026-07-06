"""AsyncSimplePinnedMemoryDAO单元测试.

测试简化置顶记忆数据访问对象的核心业务逻辑, Mock所有外部依赖（数据库、会话工厂）。
遵循单元测试设计规范：白盒测试, 专注单一功能模块, 快速反馈。
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.storage.dao.async_simple_pinned_memory_dao import AsyncSimplePinnedMemoryDAO
from src.storage.models.simple_pinned_memory import (
    SimplePinnedMemory,
    SimplePinnedMemoryType,
)


@pytest.fixture
def mock_session_factory():
    """Mock数据库会话工厂."""
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock()
    factory.return_value.__aexit__ = AsyncMock()
    return factory


@pytest.fixture
def mock_db_ops():
    """Mock数据库操作组件."""
    db_ops = MagicMock()
    db_ops.find_by_filters = AsyncMock(return_value=[])
    db_ops.bulk_create = AsyncMock(return_value=[])
    db_ops.health_check = AsyncMock(return_value=True)
    db_ops.create_with_validation = AsyncMock()
    db_ops.transaction_scope = MagicMock()
    db_ops.session_factory = MagicMock()
    db_ops.session_factory.return_value.__aenter__ = AsyncMock()
    db_ops.session_factory.return_value.__aexit__ = AsyncMock()
    return db_ops


@pytest.fixture
def pinned_dao(mock_session_factory, mock_db_ops):
    """创建AsyncSimplePinnedMemoryDAO实例."""
    dao = AsyncSimplePinnedMemoryDAO(mock_session_factory)
    dao.db_ops = mock_db_ops
    return dao


def _make_memory(
    memory_type: SimplePinnedMemoryType = SimplePinnedMemoryType.BASIC_INFO,
    content: str = "测试内容",
    user_id: str = "test_user",
    thread_id: str = "test_thread",
) -> SimplePinnedMemory:
    """构造测试用置顶记忆实例."""
    return SimplePinnedMemory(
        id=1,
        user_id=user_id,
        thread_id=thread_id,
        memory_type=memory_type,
        content=content,
        priority=50,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class TestGetMemoryByType:
    """测试按类型获取记忆记录."""

    @pytest.mark.asyncio
    async def test_get_memory_by_type_should_return_record_when_found(
        self, pinned_dao, mock_db_ops, test_user
    ):
        """测试按类型获取记忆：找到记录时应返回该记录."""
        # Arrange
        expected = _make_memory(user_id=test_user)
        mock_db_ops.find_by_filters.return_value = [expected]

        # Act
        result = await pinned_dao.get_memory_by_type(
            user_id=test_user,
            thread_id="test_thread",
            memory_type=SimplePinnedMemoryType.BASIC_INFO,
        )

        # Assert
        assert result is expected
        mock_db_ops.find_by_filters.assert_called_once()
        call_kwargs = mock_db_ops.find_by_filters.call_args[0][0]
        assert call_kwargs["user_id"] == test_user
        assert call_kwargs["memory_type"] == SimplePinnedMemoryType.BASIC_INFO

    @pytest.mark.asyncio
    async def test_get_memory_by_type_should_return_none_when_not_found(
        self, pinned_dao, mock_db_ops, test_user
    ):
        """测试按类型获取记忆：未找到时应返回None."""
        # Arrange
        mock_db_ops.find_by_filters.return_value = []

        # Act
        result = await pinned_dao.get_memory_by_type(
            user_id=test_user,
            thread_id="test_thread",
            memory_type=SimplePinnedMemoryType.PREFERENCES,
        )

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_get_memory_by_type_should_raise_on_exception(
        self, pinned_dao, mock_db_ops, test_user
    ):
        """测试按类型获取记忆：数据库异常时应抛出."""
        # Arrange
        mock_db_ops.find_by_filters.side_effect = RuntimeError("DB error")

        # Act & Assert
        with pytest.raises(RuntimeError, match="DB error"):
            await pinned_dao.get_memory_by_type(
                user_id=test_user,
                thread_id="test_thread",
                memory_type=SimplePinnedMemoryType.BASIC_INFO,
            )


def _setup_session_factory(mock_db_ops, session_mock):
    """辅助函数：在mock_db_ops上配置session_factory上下文管理器."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session_mock)
    ctx.__aexit__ = AsyncMock(return_value=None)
    mock_db_ops.session_factory.return_value = ctx


class TestGetAllMemories:
    """测试获取所有置顶记忆."""

    @pytest.mark.asyncio
    async def test_get_all_memories_should_return_list_when_records_exist(
        self, pinned_dao, mock_db_ops, test_user
    ):
        """测试获取所有记忆：应返回记录列表."""
        # Arrange
        memory1 = _make_memory(SimplePinnedMemoryType.BASIC_INFO, user_id=test_user)
        memory2 = _make_memory(SimplePinnedMemoryType.PREFERENCES, user_id=test_user)
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [memory1, memory2]
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)
        _setup_session_factory(mock_db_ops, mock_session)

        # Act
        result = await pinned_dao.get_all_memories(user_id=test_user, thread_id="t")

        # Assert
        assert len(result) == 2
        assert result[0] is memory1
        assert result[1] is memory2

    @pytest.mark.asyncio
    async def test_get_all_memories_should_return_empty_when_no_records(
        self, pinned_dao, mock_db_ops, test_user
    ):
        """测试获取所有记忆：无记录时返回空列表."""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)
        _setup_session_factory(mock_db_ops, mock_session)

        # Act
        result = await pinned_dao.get_all_memories(user_id=test_user, thread_id="t")

        # Assert
        assert result == []

    @pytest.mark.asyncio
    async def test_get_all_memories_should_raise_on_exception(
        self, pinned_dao, mock_db_ops, test_user
    ):
        """测试获取所有记忆：异常时应抛出."""
        # Arrange
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
        _setup_session_factory(mock_db_ops, mock_session)

        # Act & Assert
        with pytest.raises(RuntimeError, match="DB error"):
            await pinned_dao.get_all_memories(user_id=test_user, thread_id="t")


class TestUpsertMemory:
    """测试更新或插入记忆."""

    @pytest.mark.asyncio
    async def test_upsert_memory_should_create_when_not_existing(
        self, pinned_dao, mock_db_ops, test_user
    ):
        """测试upsert：记录不存在时应创建新记录."""
        # Arrange
        new_record = _make_memory(user_id=test_user)
        mock_db_ops.find_by_filters.return_value = []  # 不存在
        mock_db_ops.create_with_validation.return_value = new_record

        # 使用 transaction_scope 上下文管理器
        tx_mock = AsyncMock()
        mock_db_ops.transaction_scope.return_value = tx_mock
        tx_mock.__aenter__ = AsyncMock(return_value=AsyncMock())
        tx_mock.__aexit__ = AsyncMock(return_value=None)

        # Act
        result = await pinned_dao.upsert_memory(
            user_id=test_user,
            thread_id="t",
            memory_type=SimplePinnedMemoryType.BASIC_INFO,
            content="新内容",
        )

        # Assert
        assert result is new_record
        mock_db_ops.create_with_validation.assert_called_once()
        kwargs = mock_db_ops.create_with_validation.call_args[1]
        assert kwargs["user_id"] == test_user
        assert kwargs["content"] == "新内容"

    @pytest.mark.asyncio
    async def test_upsert_memory_should_raise_on_exception(
        self, pinned_dao, mock_db_ops, test_user
    ):
        """测试upsert：异常时应抛出."""
        # Arrange
        mock_db_ops.find_by_filters.side_effect = RuntimeError("DB error")
        tx_mock = AsyncMock()
        mock_db_ops.transaction_scope.return_value = tx_mock
        tx_mock.__aenter__ = AsyncMock(return_value=AsyncMock())
        tx_mock.__aexit__ = AsyncMock(return_value=None)

        # Act & Assert
        with pytest.raises(RuntimeError, match="DB error"):
            await pinned_dao.upsert_memory(
                user_id=test_user,
                thread_id="t",
                memory_type=SimplePinnedMemoryType.BASIC_INFO,
                content="内容",
            )


class TestDeleteMemory:
    """测试删除记忆."""

    @pytest.mark.asyncio
    async def test_delete_memory_should_return_true_when_deleted(
        self, pinned_dao, mock_db_ops, test_user
    ):
        """测试删除：成功删除时应返回True."""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        tx_mock = AsyncMock()
        mock_db_ops.transaction_scope.return_value = tx_mock
        tx_mock.__aenter__ = AsyncMock(return_value=mock_session)
        tx_mock.__aexit__ = AsyncMock(return_value=None)

        # Act
        result = await pinned_dao.delete_memory(
            user_id=test_user,
            thread_id="t",
            memory_type=SimplePinnedMemoryType.BASIC_INFO,
        )

        # Assert
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_memory_should_return_false_when_no_row_affected(
        self, pinned_dao, mock_db_ops, test_user
    ):
        """测试删除：未影响行时应返回False."""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute = AsyncMock(return_value=mock_result)
        tx_mock = AsyncMock()
        mock_db_ops.transaction_scope.return_value = tx_mock
        tx_mock.__aenter__ = AsyncMock(return_value=mock_session)
        tx_mock.__aexit__ = AsyncMock(return_value=None)

        # Act
        result = await pinned_dao.delete_memory(
            user_id=test_user,
            thread_id="t",
            memory_type=SimplePinnedMemoryType.BASIC_INFO,
        )

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_memory_should_raise_on_exception(
        self, pinned_dao, mock_db_ops, test_user
    ):
        """测试删除：异常时应抛出."""
        # Arrange
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
        tx_mock = AsyncMock()
        mock_db_ops.transaction_scope.return_value = tx_mock
        tx_mock.__aenter__ = AsyncMock(return_value=mock_session)
        tx_mock.__aexit__ = AsyncMock(return_value=None)

        # Act & Assert
        with pytest.raises(RuntimeError, match="DB error"):
            await pinned_dao.delete_memory(
                user_id=test_user,
                thread_id="t",
                memory_type=SimplePinnedMemoryType.BASIC_INFO,
            )


class TestBulkCreate:
    """测试批量创建."""

    @pytest.mark.asyncio
    async def test_bulk_create_should_delegate_to_db_ops(self, pinned_dao, mock_db_ops):
        """测试批量创建：应委托给db_ops."""
        # Arrange
        items = [
            {
                "user_id": "test_user",
                "thread_id": "t",
                "memory_type": SimplePinnedMemoryType.BASIC_INFO,
                "content": "内容1",
            }
        ]
        expected = [_make_memory()]
        mock_db_ops.bulk_create.return_value = expected

        # Act
        result = await pinned_dao.bulk_create(items)

        # Assert
        assert result is expected
        mock_db_ops.bulk_create.assert_called_once_with(
            items,
            required_fields=["user_id", "thread_id", "memory_type"],
        )


class TestHealthCheck:
    """测试健康检查."""

    @pytest.mark.asyncio
    async def test_health_check_should_delegate_to_db_ops(
        self, pinned_dao, mock_db_ops
    ):
        """测试健康检查：应委托给db_ops."""
        # Arrange
        mock_db_ops.health_check.return_value = True

        # Act
        result = await pinned_dao.health_check()

        # Assert
        assert result is True
        mock_db_ops.health_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check_should_return_false_when_unhealthy(
        self, pinned_dao, mock_db_ops
    ):
        """测试健康检查：不健康时返回False."""
        # Arrange
        mock_db_ops.health_check.return_value = False

        # Act
        result = await pinned_dao.health_check()

        # Assert
        assert result is False
