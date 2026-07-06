"""AsyncFileRegistryDAO 单元测试.

测试用户级文件注册表 DAO 的业务逻辑: 查询/去重/插入更新/引用计数/配额统计.
Mock 外部依赖: AsyncDatabaseOperations, SQLAlchemy session.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.storage.dao.async_file_registry_dao import AsyncFileRegistryDAO


@pytest.fixture
def mock_session_factory():
    return MagicMock()


@pytest.fixture
def dao(mock_session_factory):
    return AsyncFileRegistryDAO(mock_session_factory)


def _make_async_session_ctx(mock_session: AsyncMock) -> MagicMock:
    """构造 session_factory 异步上下文管理器 mock."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class TestGetByFileId:
    """测试按 file_id 查询."""

    @pytest.mark.asyncio
    async def test_found(self, dao):
        mock_entry = MagicMock()
        with patch.object(dao.db_ops, "find_by_filters", return_value=[mock_entry]):
            result = await dao.get_by_file_id("abc12345")
        assert result == mock_entry

    @pytest.mark.asyncio
    async def test_not_found(self, dao):
        with patch.object(dao.db_ops, "find_by_filters", return_value=[]):
            result = await dao.get_by_file_id("nonexist")
        assert result is None


class TestFindByContentHash:
    """测试按 content_hash 去重查询."""

    @pytest.mark.asyncio
    async def test_found(self, dao):
        mock_entry = MagicMock()
        with patch.object(dao.db_ops, "find_by_filters", return_value=[mock_entry]):
            result = await dao.find_by_content_hash("a" * 64)
        assert result == mock_entry

    @pytest.mark.asyncio
    async def test_not_found(self, dao):
        with patch.object(dao.db_ops, "find_by_filters", return_value=[]):
            result = await dao.find_by_content_hash("b" * 64)
        assert result is None


class TestListAll:
    """测试列出所有记录."""

    @pytest.mark.asyncio
    async def test_returns_all_entries(self, dao):
        entries = [MagicMock(), MagicMock()]
        with patch.object(dao.db_ops, "find_by_filters", return_value=entries):
            result = await dao.list_all()
        assert result == entries


class TestUpsert:
    """测试插入或更新."""

    @pytest.mark.asyncio
    async def test_create_new_entry(self, dao):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        dao.db_ops.transaction_scope = MagicMock()
        dao.db_ops.transaction_scope.return_value.__aenter__ = AsyncMock(
            return_value=mock_session,
        )
        dao.db_ops.transaction_scope.return_value.__aexit__ = AsyncMock(
            return_value=False,
        )

        data = {
            "file_id": "abc12345",
            "file_type": "image",
            "physical_path": "files/images/x.jpg",
            "filename": "x.jpg",
            "round_number": 1,
            "owner_thread_id": "t1",
            "owner_agent_id": "a1",
        }
        await dao.upsert(data)
        mock_session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_existing_entry(self, dao):
        existing = MagicMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        mock_session.execute = AsyncMock(return_value=mock_result)

        dao.db_ops.transaction_scope = MagicMock()
        dao.db_ops.transaction_scope.return_value.__aenter__ = AsyncMock(
            return_value=mock_session,
        )
        dao.db_ops.transaction_scope.return_value.__aexit__ = AsyncMock(
            return_value=False,
        )

        data = {
            "file_id": "abc12345",
            "file_type": "image",
            "physical_path": "files/images/x.jpg",
            "filename": "x.jpg",
            "round_number": 1,
            "owner_thread_id": "t1",
            "owner_agent_id": "a1",
            "brief": "updated",
        }
        await dao.upsert(data)
        mock_session.add.assert_not_called()
        assert existing.brief == "updated"


class TestDeleteByFileId:
    """测试按 file_id 删除."""

    @pytest.mark.asyncio
    async def test_deleted(self, dao, mock_session_factory):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_session_factory.return_value = _make_async_session_ctx(mock_session)

        result = await dao.delete_by_file_id("abc12345")
        assert result is True

    @pytest.mark.asyncio
    async def test_not_found(self, dao, mock_session_factory):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_session_factory.return_value = _make_async_session_ctx(mock_session)

        result = await dao.delete_by_file_id("nonexist")
        assert result is False


class TestCountByContentHash:
    """测试引用计数实时统计."""

    @pytest.mark.asyncio
    async def test_returns_count(self, dao, mock_session_factory):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 3
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value = _make_async_session_ctx(mock_session)

        result = await dao.count_by_content_hash("a" * 64)
        assert result == 3

    @pytest.mark.asyncio
    async def test_zero(self, dao, mock_session_factory):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value = _make_async_session_ctx(mock_session)

        result = await dao.count_by_content_hash("c" * 64)
        assert result == 0


class TestGetTotalUniqueSize:
    """测试去重后总大小统计."""

    @pytest.mark.asyncio
    async def test_returns_total(self, dao, mock_session_factory):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = 10240
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value = _make_async_session_ctx(mock_session)

        result = await dao.get_total_unique_size()
        assert result == 10240

    @pytest.mark.asyncio
    async def test_empty_returns_zero(self, dao, mock_session_factory):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value = _make_async_session_ctx(mock_session)

        result = await dao.get_total_unique_size()
        assert result == 0


class TestListOrderedByCreated:
    """测试按创建时间升序列出."""

    @pytest.mark.asyncio
    async def test_returns_ordered_entries(self, dao, mock_session_factory):
        entries = [MagicMock(), MagicMock()]
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = entries
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value = _make_async_session_ctx(mock_session)

        result = await dao.list_ordered_by_created()
        assert result == entries


class TestListRecentByType:
    """测试按类型列出最近文件."""

    @pytest.mark.asyncio
    async def test_returns_recent_images(self, dao, mock_session_factory):
        entries = [MagicMock()]
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = entries
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value = _make_async_session_ctx(mock_session)

        result = await dao.list_recent_by_type("image", limit=5)
        assert result == entries
