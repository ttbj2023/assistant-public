"""AsyncDatabaseManager单元测试.

测试职责: 验证异步数据库管理器的核心功能逻辑
测试范围: URL转换、引擎管理、会话工厂、表创建、健康检查
Mock策略: Mock SQLAlchemy引擎、文件系统、路径解析器，保留业务逻辑
测试价值: 确保数据库管理器的正确性和资源管理

⚠️ 测试重点:
- 验证URL转换逻辑
- 验证引擎和会话工厂的懒加载和缓存
- 验证表创建的正确性
- 验证健康检查功能
- 验证资源清理
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import pytest

from src.storage.dao.async_database_manager import (
    AsyncDatabaseManager,
    _DEFAULT_MAX_OVERFLOW,
    _DEFAULT_POOL_SIZE,
    create_async_conversation_history_db_manager,
    create_async_pinned_memory_db_manager,
    create_async_todo_db_manager,
)

# ==================== TestAsyncDatabaseManagerEngine ====================


class TestAsyncDatabaseManagerEngine:
    """测试AsyncDatabaseManager引擎属性"""

    @pytest.fixture
    def manager(self):
        """创建管理器实例"""
        return AsyncDatabaseManager(":memory:")

    @patch("src.storage.dao.async_database_manager.event")
    def test_engine_file_database_should_configure_pool_and_pragma(
        self, mock_event, manager
    ):
        """测试engine属性：文件数据库应配置连接池和PRAGMA.

        使用临时目录避免污染项目目录.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.db"
            file_manager = AsyncDatabaseManager(f"sqlite:///{db_path}")

            with patch(
                "src.storage.dao.async_database_manager.create_async_engine"
            ) as mock_create:
                mock_engine = Mock()
                mock_engine.sync_engine = Mock()
                mock_create.return_value = mock_engine

                _ = file_manager.engine

                # 验证调用create_async_engine with pool参数
                call_args = mock_create.call_args
                assert call_args.kwargs["pool_size"] == _DEFAULT_POOL_SIZE
                assert call_args.kwargs["max_overflow"] == _DEFAULT_MAX_OVERFLOW
                # 验证event监听器被注册
                mock_event.listens_for.assert_called_once()

    @patch("src.storage.dao.async_database_manager.event")
    def test_engine_memory_database_should_not_configure_pool(
        self, mock_event, manager
    ):
        """测试engine属性：内存数据库不应配置连接池"""
        with patch(
            "src.storage.dao.async_database_manager.create_async_engine"
        ) as mock_create:
            mock_engine = Mock()
            mock_create.return_value = mock_engine

            _ = manager.engine

            # 验证不包含pool参数
            call_args = mock_create.call_args
            assert "pool_size" not in call_args.kwargs
            # 验证不注册event监听器
            mock_event.listens_for.assert_not_called()


# ==================== TestAsyncDatabaseManagerCreateTables ====================


class TestAsyncDatabaseManagerCreateTables:
    """测试AsyncDatabaseManager创建表方法"""

    @pytest.fixture
    def manager(self):
        """创建管理器实例"""
        manager = AsyncDatabaseManager(":memory:")
        # Mock engine以避免真实数据库连接
        manager._engine = Mock()
        return manager

    @pytest.mark.asyncio
    async def test_create_tables_with_specific_tables(self, manager):
        """测试创建表：应创建指定的表"""
        # Mock连接和表模型
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        manager._engine.begin.return_value = mock_conn

        mock_table = Mock()
        mock_table.__tablename__ = "test_table"
        mock_table.__table__ = Mock()

        await manager.create_tables([mock_table])

        # 验证run_sync被调用
        mock_conn.run_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_tables_without_tables_should_create_all(self, manager):
        """测试创建表：未指定表时应创建所有"""
        with patch("src.storage.dao.async_database_manager.SQLModel") as mock_sqlmodel:
            mock_conn = AsyncMock()
            mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_conn.__aexit__ = AsyncMock(return_value=None)
            manager._engine.begin.return_value = mock_conn

            await manager.create_tables(None)

            # 验证调用metadata.create_all
            mock_conn.run_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_tables_with_invalid_table_should_skip(self, manager):
        """测试创建表：无效表模型应跳过"""
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        manager._engine.begin.return_value = mock_conn

        # 没有__table__属性的模型
        mock_table = Mock(spec=[])

        # 不应抛出异常
        await manager.create_tables([mock_table])


# ==================== TestAsyncDatabaseManagerHealthCheck ====================


class TestAsyncDatabaseManagerHealthCheck:
    """测试AsyncDatabaseManager健康检查方法"""

    @pytest.fixture
    def manager(self):
        """创建管理器实例"""
        manager = AsyncDatabaseManager(":memory:")
        manager._session_factory = Mock()
        return manager

    @pytest.mark.asyncio
    async def test_health_check_success_should_return_true(self, manager):
        """测试健康检查：成功应返回True"""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        manager._session_factory.return_value = mock_session

        result = await manager.health_check()

        assert result is True
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check_failure_should_return_false(self, manager):
        """测试健康检查：失败应返回False"""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.execute.side_effect = Exception("DB Error")
        manager._session_factory.return_value = mock_session

        result = await manager.health_check()

        assert result is False


# ==================== TestAsyncDatabaseManagerClose ====================


class TestAsyncDatabaseManagerClose:
    """测试AsyncDatabaseManager关闭方法"""

    @pytest.mark.asyncio
    async def test_close_should_dispose_engine(self):
        """测试关闭：应释放引擎"""
        manager = AsyncDatabaseManager(":memory:")
        manager._engine = AsyncMock()
        manager._engine.dispose = AsyncMock()

        await manager.close()

        manager._engine.dispose.assert_called_once()


# ==================== TestAsyncDatabaseManagerEnsureDirectory ====================


class TestAsyncDatabaseManagerEnsureDirectory:
    """测试AsyncDatabaseManager目录创建方法"""

    @pytest.fixture
    def file_manager(self):
        """创建文件数据库管理器"""
        return AsyncDatabaseManager("sqlite:///data/user/thread/agent/test.db")

    @patch("src.storage.dao.async_database_manager.get_user_path_resolver")
    def test_ensure_directory_should_use_path_resolver(
        self, mock_get_resolver, file_manager
    ):
        """测试目录创建：应使用path_resolver"""
        mock_resolver = Mock()
        mock_resolver.get_database_path = Mock()
        mock_get_resolver.return_value = mock_resolver

        file_manager._ensure_database_directory()

        mock_resolver.get_database_path.assert_called_once()

    @patch("src.storage.dao.async_database_manager.get_user_path_resolver")
    @patch("pathlib.Path.mkdir")
    def test_ensure_directory_fallback_should_mkdir(
        self, mock_mkdir, mock_get_resolver, file_manager
    ):
        """测试目录创建：回退方案应直接mkdir"""
        # path_resolver抛出异常
        mock_get_resolver.side_effect = Exception("Resolver error")

        file_manager._ensure_database_directory()

        # 验证调用mkdir
        mock_mkdir.assert_called()


# ==================== TestFactoryFunctions ====================


class TestFactoryFunctions:
    """测试工厂函数"""

    @patch("src.storage.dao.async_database_manager.get_database_path")
    async def test_create_async_todo_db_manager_should_create_manager(
        self, mock_get_path
    ):
        """测试TODO管理器：应创建管理器并调用create_tables"""
        mock_get_path.return_value = "/data/user/thread/todo.db"

        with patch.object(AsyncDatabaseManager, "create_tables", new=AsyncMock()):
            manager = await create_async_todo_db_manager(
                "user1", "thread1", agent_id="test-agent"
            )

            assert isinstance(manager, AsyncDatabaseManager)
            manager.create_tables.assert_called_once()

    @patch("src.storage.dao.async_database_manager.get_database_path")
    async def test_create_async_pinned_memory_db_manager_should_create_manager(
        self, mock_get_path
    ):
        """测试置顶记忆管理器：应创建管理器并调用create_tables + 迁移OTHER_INFO"""
        mock_get_path.return_value = "/data/user/thread/pinned_memory.db"

        mock_conn = AsyncMock()
        mock_conn.execute.return_value = Mock(rowcount=0)
        mock_engine = Mock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_conn
        mock_cm.__aexit__.return_value = None
        mock_engine.begin = Mock(return_value=mock_cm)

        with (
            patch.object(AsyncDatabaseManager, "create_tables", new=AsyncMock()),
            patch.object(
                AsyncDatabaseManager, "engine", new_callable=PropertyMock
            ) as mock_engine_prop,
        ):
            mock_engine_prop.return_value = mock_engine

            manager = await create_async_pinned_memory_db_manager(
                "user1", "thread1", agent_id="test-agent"
            )

            assert isinstance(manager, AsyncDatabaseManager)
            manager.create_tables.assert_called_once()
            mock_conn.execute.assert_called_once()

    @patch("src.storage.dao.async_database_manager.get_database_path")
    async def test_create_async_conversation_history_db_manager_should_create_manager(
        self, mock_get_path
    ):
        """测试对话历史管理器：应创建管理器并调用create_tables"""
        mock_get_path.return_value = "/data/user/thread/conversation_history.db"

        with patch.object(AsyncDatabaseManager, "create_tables", new=AsyncMock()):
            manager = await create_async_conversation_history_db_manager(
                "user1", "thread1", agent_id="test-agent"
            )

            assert isinstance(manager, AsyncDatabaseManager)
            manager.create_tables.assert_called_once()
