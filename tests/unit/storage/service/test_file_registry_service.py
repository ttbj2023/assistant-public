"""FileRegistryService 单元测试.

测试用户级文件注册表服务的业务逻辑.
Mock 外部依赖: AsyncFileRegistryDAO.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.storage.service.file_registry_service import FileRegistryService


@pytest.fixture
def mock_session_factory():
    return MagicMock()


@pytest.fixture
def service(mock_session_factory):
    return FileRegistryService(mock_session_factory, user_id="user1")


class TestUpsert:
    """测试插入或更新."""

    @pytest.mark.asyncio
    async def test_converts_entry_to_dict(self, service):
        mock_entry = MagicMock()
        mock_entry.model_dump.return_value = {
            "file_id": "abc12345",
            "file_type": "image",
        }
        service.dao.upsert = AsyncMock()

        await service.upsert(mock_entry)
        service.dao.upsert.assert_called_once_with(
            {"file_id": "abc12345", "file_type": "image"},
        )


class TestCountReferences:
    """测试引用计数实时统计 (去重决策依据)."""

    @pytest.mark.asyncio
    async def test_delegates_to_dao(self, service):
        service.dao.count_by_content_hash = AsyncMock(return_value=2)
        result = await service.count_references("a" * 64)
        assert result == 2
        service.dao.count_by_content_hash.assert_called_once_with("a" * 64)

    @pytest.mark.asyncio
    async def test_zero_means_safe_to_delete(self, service):
        service.dao.count_by_content_hash = AsyncMock(return_value=0)
        result = await service.count_references("b" * 64)
        assert result == 0


class TestDelegation:
    """测试各方法正确委托 DAO."""

    @pytest.mark.asyncio
    async def test_get(self, service):
        mock_entry = MagicMock()
        service.dao.get_by_file_id = AsyncMock(return_value=mock_entry)
        result = await service.get("abc12345")
        assert result == mock_entry

    @pytest.mark.asyncio
    async def test_find_by_content_hash(self, service):
        mock_entry = MagicMock()
        service.dao.find_by_content_hash = AsyncMock(return_value=mock_entry)
        result = await service.find_by_content_hash("a" * 64)
        assert result == mock_entry

    @pytest.mark.asyncio
    async def test_list_all(self, service):
        entries = [MagicMock(), MagicMock()]
        service.dao.list_all = AsyncMock(return_value=entries)
        result = await service.list_all()
        assert result == entries

    @pytest.mark.asyncio
    async def test_list_recent_images(self, service):
        entries = [MagicMock()]
        service.dao.list_recent_by_type = AsyncMock(return_value=entries)
        result = await service.list_recent_images(limit=5)
        service.dao.list_recent_by_type.assert_called_once_with("image", 5)
        assert result == entries

    @pytest.mark.asyncio
    async def test_list_recent_documents(self, service):
        entries = [MagicMock()]
        service.dao.list_recent_by_type = AsyncMock(return_value=entries)
        result = await service.list_recent_documents(limit=3)
        service.dao.list_recent_by_type.assert_called_once_with("document", 3)
        assert result == entries

    @pytest.mark.asyncio
    async def test_delete(self, service):
        service.dao.delete_by_file_id = AsyncMock(return_value=True)
        result = await service.delete("abc12345")
        assert result is True

    @pytest.mark.asyncio
    async def test_list_ordered_by_created(self, service):
        entries = [MagicMock()]
        service.dao.list_ordered_by_created = AsyncMock(return_value=entries)
        result = await service.list_ordered_by_created()
        assert result == entries

    @pytest.mark.asyncio
    async def test_get_total_unique_size(self, service):
        service.dao.get_total_unique_size = AsyncMock(return_value=5120)
        result = await service.get_total_unique_size()
        assert result == 5120
