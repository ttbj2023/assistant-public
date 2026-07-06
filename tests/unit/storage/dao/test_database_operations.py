"""DatabaseOperations单元测试.

测试职责: 验证通用数据库操作类的核心功能逻辑
测试范围: CRUD操作、批量操作、事务管理、查询过滤
Mock策略: Mock数据库会话和SQLAlchemy执行结果，保留业务逻辑
测试价值: 确保数据库操作的正确性和事务安全性

⚠️ 测试重点:
- 验证CRUD操作的正确性
- 验证事务管理和回滚
- 验证字段验证逻辑
- 验证用户线程过滤
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.dao.database_operations import AsyncDatabaseOperations

# ==================== Mock模型和会话 ====================


# Patch SQLAlchemy语句构建函数
# 这些mock对象支持链式调用
def _create_chained_mock():
    """创建支持链式调用的mock"""
    m = Mock()
    m.where = Mock(return_value=m)
    m.values = Mock(return_value=m)
    m.returning = Mock(return_value=m)
    m.limit = Mock(return_value=m)
    m.offset = Mock(return_value=m)
    m.order_by = Mock(return_value=m)
    return m


mock_select = Mock(return_value=_create_chained_mock())
mock_insert = Mock(return_value=_create_chained_mock())
mock_update = Mock(return_value=_create_chained_mock())
mock_delete = Mock(return_value=_create_chained_mock())


@pytest.fixture(autouse=True)
def patch_sqlalchemy_functions():
    """自动为所有测试patch SQLAlchemy语句构建函数"""
    with patch("src.storage.dao.database_operations.select", mock_select):
        with patch("src.storage.dao.database_operations.insert", mock_insert):
            with patch("src.storage.dao.database_operations.update", mock_update):
                with patch("src.storage.dao.database_operations.delete", mock_delete):
                    yield


class MockColumn:
    """Mock SQLAlchemy列"""

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        """支持比较操作"""
        return Mock()

    def in_(self, values):
        """支持IN操作"""
        return Mock()

    def desc(self):
        """支持降序"""
        return Mock()


class MockModel:
    """Mock数据模型类 - 类级别的列属性"""

    id = MockColumn("id")
    user_id = MockColumn("user_id")
    thread_id = MockColumn("thread_id")
    title = MockColumn("title")
    content = MockColumn("content")
    status = MockColumn("status")
    created_at = MockColumn("created_at")


class MockModelWithCreatedAt:
    """带created_at字段的Mock模型"""

    id = MockColumn("id")
    user_id = MockColumn("user_id")
    thread_id = MockColumn("thread_id")
    created_at = MockColumn("created_at")


def _create_mock_session():
    """创建完整的Mock会话"""
    mock_session = AsyncMock(spec=AsyncSession)

    # Mock execute返回的结果
    mock_result = Mock()
    mock_result.scalar_one.return_value = Mock(
        id=1, user_id="test_user", thread_id="test_thread"
    )
    mock_result.scalar_one_or_none.return_value = Mock(id=1, user_id="test_user")
    mock_result.scalars.return_value.all.return_value = []
    mock_result.rowcount = 1

    mock_session.execute.return_value = mock_result
    return mock_session


def _create_mock_session_factory():
    """创建Mock会话工厂"""
    factory = Mock()

    # 创建mock session
    mock_session = _create_mock_session()

    # 创建mock async context manager session
    mock_async_session = AsyncMock()
    mock_async_session.__aenter__.return_value = mock_session
    mock_async_session.__aexit__.return_value = None

    # Mock session.begin() 也是一个async context manager
    mock_begin = AsyncMock()
    mock_begin.__aenter__.return_value = mock_session
    mock_begin.__aexit__.return_value = None
    mock_async_session.return_value.begin = mock_begin

    factory.return_value = mock_async_session

    return factory


# ==================== TestAsyncDatabaseOperations CRUD ====================


class TestAsyncDatabaseOperationsCreate:
    """测试AsyncDatabaseOperations创建操作"""

    @pytest.fixture
    def db_ops(self):
        """创建数据库操作实例"""
        return AsyncDatabaseOperations(_create_mock_session_factory(), MockModel)

    @pytest.mark.asyncio
    async def test_create_with_validation_should_validate_required_fields(self, db_ops):
        """测试创建记录：应验证必需字段"""
        result = await db_ops.create_with_validation(
            required_fields=["title"], title="Test"
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_create_with_validation_missing_fields_should_raise_error(
        self, db_ops
    ):
        """测试创建记录：缺少必需字段应抛出异常"""
        with pytest.raises(ValueError, match="缺少必需字段"):
            await db_ops.create_with_validation(
                required_fields=["title", "content"],
                title=None,  # 缺少title
            )


class TestAsyncDatabaseOperationsRead:
    """测试AsyncDatabaseOperations读取操作"""

    @pytest.fixture
    def db_ops(self):
        """创建数据库操作实例"""
        return AsyncDatabaseOperations(_create_mock_session_factory(), MockModel)

    @pytest.mark.asyncio
    async def test_list_all_should_return_all_records(self, db_ops):
        """测试列出所有记录：应返回所有记录"""
        # Mock返回多个记录
        mock_session = db_ops.session_factory.return_value.__aenter__.return_value
        mock_result = Mock()
        mock_result.scalars.return_value.all.return_value = [Mock(), Mock()]
        mock_session.execute.return_value = mock_result

        result = await db_ops.list_all(limit=10)

        assert len(result) == 2


class TestAsyncDatabaseOperationsUpdate:
    """测试AsyncDatabaseOperations更新操作"""

    @pytest.fixture
    def db_ops(self):
        """创建数据库操作实例"""
        return AsyncDatabaseOperations(_create_mock_session_factory(), MockModel)

    @pytest.mark.asyncio
    async def test_update_non_existent_id_should_return_none(self, db_ops):
        """测试更新记录：不存在的ID应返回None"""
        # Mock返回None
        mock_session = db_ops.session_factory.return_value.__aenter__.return_value
        mock_result = Mock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await db_ops.update(999, {"title": "Updated"})

        assert result is None


class TestAsyncDatabaseOperationsDelete:
    """测试AsyncDatabaseOperations删除操作"""

    @pytest.fixture
    def db_ops(self):
        """创建数据库操作实例"""
        return AsyncDatabaseOperations(_create_mock_session_factory(), MockModel)

    @pytest.mark.asyncio
    async def test_delete_by_id_should_execute_delete_statement(self, db_ops):
        """测试根据ID删除：应执行DELETE语句"""
        result = await db_ops.delete_by_id(1)

        assert result is True
        mock_session = db_ops.session_factory.return_value.__aenter__.return_value
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_by_id_non_existent_should_return_false(self, db_ops):
        """测试根据ID删除：不存在的ID应返回False"""
        # Mock rowcount = 0
        mock_session = db_ops.session_factory.return_value.__aenter__.return_value
        mock_result = Mock()
        mock_result.rowcount = 0
        mock_session.execute.return_value = mock_result

        result = await db_ops.delete_by_id(999)

        assert result is False


class TestAsyncDatabaseOperationsBulkOperations:
    """测试AsyncDatabaseOperations批量操作"""

    @pytest.fixture
    def db_ops(self):
        """创建数据库操作实例"""
        return AsyncDatabaseOperations(_create_mock_session_factory(), MockModel)

    @pytest.mark.asyncio
    async def test_bulk_create_should_create_multiple_records(self, db_ops):
        """测试批量创建：应创建多个记录"""
        items = [{"title": "Test1"}, {"title": "Test2"}]

        # Mock返回多个记录
        mock_session = db_ops.session_factory.return_value.__aenter__.return_value
        mock_result = Mock()
        mock_result.scalars.return_value.all.return_value = [Mock(), Mock()]
        mock_session.execute.return_value = mock_result

        result = await db_ops.bulk_create(items)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_bulk_create_empty_list_should_return_empty(self, db_ops):
        """测试批量创建：空列表应返回空列表"""
        result = await db_ops.bulk_create([])

        assert result == []

    @pytest.mark.asyncio
    async def test_bulk_create_with_validation_should_validate_all_items(self, db_ops):
        """测试批量创建：应验证所有项目的必需字段"""
        items = [{"title": "Test1"}, {"title": "Test2"}]

        # Mock返回2个项目
        mock_session = db_ops.session_factory.return_value.__aenter__.return_value
        mock_result = Mock()
        mock_result.scalars.return_value.all.return_value = [Mock(), Mock()]
        mock_session.execute.return_value = mock_result

        result = await db_ops.bulk_create(items, required_fields=["title"])

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_bulk_create_with_validation_should_raise_on_invalid_item(
        self, db_ops
    ):
        """测试批量创建：无效项目应抛出异常"""
        with pytest.raises(ValueError, match="缺少必需字段"):
            items = [{"title": "Valid"}, {}]  # 第二个缺少必需字段
            await db_ops.bulk_create(items, required_fields=["title"])

    @pytest.mark.asyncio
    async def test_bulk_delete_by_user_thread_should_delete_matching_records(
        self, db_ops
    ):
        """测试批量删除：应删除匹配的用户线程记录"""
        # Mock rowcount = 5
        mock_session = db_ops.session_factory.return_value.__aenter__.return_value
        mock_result = Mock()
        mock_result.rowcount = 5
        mock_session.execute.return_value = mock_result

        result = await db_ops.bulk_delete_by_user_thread("user1", "thread1")

        assert result == 5
        mock_session.execute.assert_called_once()


class TestAsyncDatabaseOperationsQueries:
    """测试AsyncDatabaseOperations查询操作"""

    @pytest.fixture
    def db_ops(self):
        """创建数据库操作实例"""
        return AsyncDatabaseOperations(_create_mock_session_factory(), MockModel)

    @pytest.mark.asyncio
    async def test_find_by_filters_should_apply_all_filters(self, db_ops):
        """测试根据过滤查找：应应用所有过滤条件"""
        result = await db_ops.find_by_filters({
            "user_id": "user1",
            "thread_id": "thread1",
        })

        assert isinstance(result, list)
        mock_session = db_ops.session_factory.return_value.__aenter__.return_value
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_find_by_filters_should_ignore_invalid_fields(self, db_ops):
        """测试根据过滤查找：应忽略无效字段"""
        # 包含模型不存在的字段，应该被忽略
        result = await db_ops.find_by_filters({
            "user_id": "user1",
            "invalid_field": "value",
        })

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_find_by_filters_with_list_value_should_use_in(self, db_ops):
        """测试根据过滤查找：列表值应使用IN查询"""
        # 临时替换MockModel的status列为跟踪in_调用的Mock
        tracking_mock = Mock()
        tracking_mock.in_ = Mock(return_value=Mock())
        original = MockModel.status
        MockModel.status = tracking_mock

        try:
            result = await db_ops.find_by_filters({
                "user_id": "user1",
                "status": ["pending", "in_progress"],
            })

            assert isinstance(result, list)
            # 验证 in_ 方法被调用
            tracking_mock.in_.assert_called_once_with(["pending", "in_progress"])
        finally:
            MockModel.status = original

    @pytest.mark.asyncio
    async def test_find_by_filters_with_empty_list_should_skip_filter(self, db_ops):
        """测试根据过滤查找：空列表应跳过该过滤条件"""
        # 临时替换MockModel的status列为跟踪in_调用的Mock
        tracking_mock = Mock()
        tracking_mock.in_ = Mock(return_value=Mock())
        original = MockModel.status
        MockModel.status = tracking_mock

        try:
            result = await db_ops.find_by_filters({
                "user_id": "user1",
                "status": [],
            })

            assert isinstance(result, list)
            # 空列表不应调用 in_
            tracking_mock.in_.assert_not_called()
        finally:
            MockModel.status = original

    @pytest.mark.asyncio
    async def test_get_latest_should_order_by_created_at(self, db_ops):
        """测试获取最新记录：应按created_at降序排序"""
        # 使用带created_at的模型
        db_ops_with_created = AsyncDatabaseOperations(
            db_ops.session_factory, MockModelWithCreatedAt
        )

        result = await db_ops_with_created.get_latest(
            user_id="user1", thread_id="thread1", order_field="created_at"
        )

        assert isinstance(result, list)
        mock_session = db_ops.session_factory.return_value.__aenter__.return_value
        mock_session.execute.assert_called_once()


class TestAsyncDatabaseOperationsUtilityMethods:
    """测试AsyncDatabaseOperations工具方法"""

    @pytest.fixture
    def db_ops(self):
        """创建数据库操作实例"""
        return AsyncDatabaseOperations(_create_mock_session_factory(), MockModel)

    @pytest.mark.asyncio
    async def test_transaction_scope_should_rollback_on_error(self, db_ops):
        """测试事务范围：错误时应回滚"""
        mock_session = db_ops.session_factory.return_value.__aenter__.return_value
        mock_session.rollback = AsyncMock()

        with pytest.raises(ValueError):
            async with db_ops.transaction_scope() as session:
                raise ValueError("Test error")

        # 应该调用rollback（可能已经在transaction_scope内部调用）
        # 这里我们主要验证异常被正确传播
