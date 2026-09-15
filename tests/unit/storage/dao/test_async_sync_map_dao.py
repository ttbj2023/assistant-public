"""AsyncSyncMapDAO / AsyncSyncSettingDAO 单元测试.

Mock 策略: Mock AsyncDatabaseOperations (与 calendar DAO 测试同款),
验证 upsert 分支 (存在→update, 不存在→create) 与查询/删除委托.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.storage.dao.async_sync_map_dao import AsyncSyncMapDAO
from src.storage.dao.async_sync_setting_dao import AsyncSyncSettingDAO
from src.storage.models.sync_map import SyncItemKind, SyncMap
from src.storage.models.sync_setting import SyncSetting


@pytest.fixture
def map_dao():
    return AsyncSyncMapDAO(MagicMock())


@pytest.fixture
def setting_dao():
    return AsyncSyncSettingDAO(MagicMock())


def _mapping(**overrides) -> SyncMap:
    fields = {
        "id": 7,
        "user_id": "test_user",
        "kind": SyncItemKind.TODO,
        "local_id": 1,
        "remote_id": "rid-1",
    }
    fields.update(overrides)
    return SyncMap(**fields)


class TestSyncMapDAO:
    """AsyncSyncMapDAO 测试."""

    @pytest.mark.asyncio
    async def test_upsert_映射不存在_走create(self, map_dao):
        with (
            patch.object(
                map_dao.db_ops,
                "find_by_filters",
                new=AsyncMock(return_value=[]),
            ) as mock_find,
            patch.object(
                map_dao.db_ops,
                "create_with_validation",
                new=AsyncMock(return_value=_mapping()),
            ) as mock_create,
        ):
            result = await map_dao.upsert(
                user_id="test_user",
                kind=SyncItemKind.TODO,
                local_id=1,
                remote_id="rid-1",
            )

        assert result.remote_id == "rid-1"
        mock_find.assert_awaited_once()
        mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_映射已存在_走update(self, map_dao):
        existing = _mapping()
        with (
            patch.object(
                map_dao.db_ops,
                "find_by_filters",
                new=AsyncMock(return_value=[existing]),
            ),
            patch.object(
                map_dao.db_ops,
                "update",
                new=AsyncMock(return_value=_mapping(remote_id="rid-2")),
            ) as mock_update,
        ):
            result = await map_dao.upsert(
                user_id="test_user",
                kind=SyncItemKind.TODO,
                local_id=1,
                remote_id="rid-2",
            )

        assert result.remote_id == "rid-2"
        mock_update.assert_awaited_once_with(7, {"remote_id": "rid-2"})

    @pytest.mark.asyncio
    async def test_upsert_携带hash与时间_更新字段齐全(self, map_dao):
        from datetime import UTC, datetime

        now = datetime(2026, 9, 16, tzinfo=UTC)
        with (
            patch.object(
                map_dao.db_ops,
                "find_by_filters",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(
                map_dao.db_ops,
                "create_with_validation",
                new=AsyncMock(return_value=_mapping()),
            ) as mock_create,
        ):
            await map_dao.upsert(
                user_id="test_user",
                kind=SyncItemKind.TODO,
                local_id=1,
                remote_id="rid-1",
                content_hash="abc123",
                last_synced_at=now,
            )

        kwargs = mock_create.call_args.kwargs
        assert kwargs["content_hash"] == "abc123"
        assert kwargs["last_synced_at"] == now

    @pytest.mark.asyncio
    async def test_get_by_local_按过滤委托查询(self, map_dao):
        with patch.object(
            map_dao.db_ops,
            "find_by_filters",
            new=AsyncMock(return_value=[_mapping()]),
        ) as mock_find:
            result = await map_dao.get_by_local(
                "test_user",
                SyncItemKind.TODO,
                1,
            )

        assert result is not None and result.local_id == 1
        filters = mock_find.call_args.args[0]
        assert filters == {
            "user_id": "test_user",
            "kind": SyncItemKind.TODO,
            "local_id": 1,
        }

    @pytest.mark.asyncio
    async def test_get_by_local_无结果_返回None(self, map_dao):
        with patch.object(
            map_dao.db_ops,
            "find_by_filters",
            new=AsyncMock(return_value=[]),
        ):
            assert await map_dao.get_by_local("test_user", SyncItemKind.TODO, 1) is None

    @pytest.mark.asyncio
    async def test_get_by_remote_按远端id查询(self, map_dao):
        with patch.object(
            map_dao.db_ops,
            "find_by_filters",
            new=AsyncMock(return_value=[_mapping()]),
        ) as mock_find:
            result = await map_dao.get_by_remote(
                "test_user",
                SyncItemKind.EVENT,
                "rid-1",
            )

        assert result is not None
        filters = mock_find.call_args.args[0]
        assert filters["remote_id"] == "rid-1"

    @pytest.mark.asyncio
    async def test_delete_by_local_存在时删除返回True(self, map_dao):
        with (
            patch.object(
                map_dao.db_ops,
                "find_by_filters",
                new=AsyncMock(return_value=[_mapping()]),
            ),
            patch.object(
                map_dao.db_ops,
                "delete_by_id",
                new=AsyncMock(return_value=True),
            ) as mock_delete,
        ):
            assert (
                await map_dao.delete_by_local(
                    "test_user",
                    SyncItemKind.TODO,
                    1,
                )
                is True
            )

        mock_delete.assert_awaited_once_with(7)

    @pytest.mark.asyncio
    async def test_delete_by_local_不存在返回False(self, map_dao):
        with patch.object(
            map_dao.db_ops,
            "find_by_filters",
            new=AsyncMock(return_value=[]),
        ):
            assert (
                await map_dao.delete_by_local(
                    "test_user",
                    SyncItemKind.TODO,
                    1,
                )
                is False
            )

    @pytest.mark.asyncio
    async def test_delete_by_remote_存在时删除返回True(self, map_dao):
        with (
            patch.object(
                map_dao.db_ops,
                "find_by_filters",
                new=AsyncMock(return_value=[_mapping()]),
            ),
            patch.object(
                map_dao.db_ops,
                "delete_by_id",
                new=AsyncMock(return_value=True),
            ) as mock_delete,
        ):
            assert (
                await map_dao.delete_by_remote(
                    "test_user",
                    SyncItemKind.TODO,
                    "rid-1",
                )
                is True
            )

        mock_delete.assert_awaited_once_with(7)

    @pytest.mark.asyncio
    async def test_list_by_kind_委托过滤查询(self, map_dao):
        with patch.object(
            map_dao.db_ops,
            "find_by_filters",
            new=AsyncMock(return_value=[_mapping()]),
        ) as mock_find:
            result = await map_dao.list_by_kind("test_user", SyncItemKind.TODO)

        assert len(result) == 1
        filters = mock_find.call_args.args[0]
        assert filters["kind"] == SyncItemKind.TODO


class TestSyncSettingDAO:
    """AsyncSyncSettingDAO 测试."""

    @pytest.mark.asyncio
    async def test_get_value_不存在_返回None(self, setting_dao):
        with patch.object(
            setting_dao.db_ops,
            "find_by_filters",
            new=AsyncMock(return_value=[]),
        ):
            assert await setting_dao.get_value("test_user", "todo_list_id") is None

    @pytest.mark.asyncio
    async def test_get_value_存在_返回值(self, setting_dao):
        setting = SyncSetting(
            id=3,
            user_id="test_user",
            key="todo_list_id",
            value="lid-1",
        )
        with patch.object(
            setting_dao.db_ops,
            "find_by_filters",
            new=AsyncMock(return_value=[setting]),
        ):
            assert await setting_dao.get_value("test_user", "todo_list_id") == "lid-1"

    @pytest.mark.asyncio
    async def test_set_value_不存在_创建(self, setting_dao):
        with (
            patch.object(
                setting_dao.db_ops,
                "find_by_filters",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(
                setting_dao.db_ops,
                "create",
                new=AsyncMock(
                    return_value=SyncSetting(
                        user_id="test_user",
                        key="k",
                        value="v",
                    ),
                ),
            ) as mock_create,
        ):
            await setting_dao.set_value("test_user", "k", "v")

        mock_create.assert_awaited_once_with(
            user_id="test_user",
            key="k",
            value="v",
        )

    @pytest.mark.asyncio
    async def test_set_value_已存在_更新(self, setting_dao):
        existing = SyncSetting(id=3, user_id="test_user", key="k", value="old")
        with (
            patch.object(
                setting_dao.db_ops,
                "find_by_filters",
                new=AsyncMock(return_value=[existing]),
            ),
            patch.object(
                setting_dao.db_ops,
                "update",
                new=AsyncMock(return_value=existing),
            ) as mock_update,
        ):
            await setting_dao.set_value("test_user", "k", "new")

        mock_update.assert_awaited_once_with(3, {"value": "new"})

    @pytest.mark.asyncio
    async def test_delete_value_存在时删除返回True(self, setting_dao):
        existing = SyncSetting(id=3, user_id="test_user", key="k", value="v")
        with (
            patch.object(
                setting_dao.db_ops,
                "find_by_filters",
                new=AsyncMock(return_value=[existing]),
            ),
            patch.object(
                setting_dao.db_ops,
                "delete_by_id",
                new=AsyncMock(return_value=True),
            ) as mock_delete,
        ):
            assert await setting_dao.delete_value("test_user", "k") is True

        mock_delete.assert_awaited_once_with(3)

    @pytest.mark.asyncio
    async def test_delete_value_不存在返回False(self, setting_dao):
        with patch.object(
            setting_dao.db_ops,
            "find_by_filters",
            new=AsyncMock(return_value=[]),
        ):
            assert await setting_dao.delete_value("test_user", "k") is False
