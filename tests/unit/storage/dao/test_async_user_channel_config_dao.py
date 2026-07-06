"""AsyncUserChannelConfigDAO 单元测试.

测试用户渠道配置DAO的业务逻辑: CRUD操作, 默认配置管理.
Mock外部依赖: AsyncDatabaseOperations, SQLAlchemy session.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.storage.dao.async_user_channel_config_dao import AsyncUserChannelConfigDAO


@pytest.fixture
def mock_session_factory():
    return MagicMock()


@pytest.fixture
def dao(mock_session_factory):
    return AsyncUserChannelConfigDAO(mock_session_factory)


class TestCreateConfig:
    """测试创建配置."""

    @pytest.mark.asyncio
    async def test_create_with_dict_config(self, dao):
        config_dict = {"target": "user123"}
        mock_result = MagicMock()

        with patch.object(
            dao.db_ops, "create_with_validation", return_value=mock_result
        ) as mock_create:
            result = await dao.create_config("u1", "wechat", config_dict)

        assert result == mock_result
        call_kwargs = mock_create.call_args
        assert json.loads(call_kwargs.kwargs["config"]) == config_dict

    @pytest.mark.asyncio
    async def test_create_with_string_config(self, dao):
        config_str = '{"target": "user123"}'
        mock_result = MagicMock()

        with patch.object(
            dao.db_ops, "create_with_validation", return_value=mock_result
        ):
            result = await dao.create_config("u1", "wechat", config_str)

        assert result == mock_result


class TestGetById:
    """测试按ID查询."""

    @pytest.mark.asyncio
    async def test_found(self, dao):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_entry = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_entry
        mock_session.execute = AsyncMock(return_value=mock_result)

        dao.session_factory = MagicMock()
        dao.session_factory.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        dao.session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await dao.get_by_id(1)
        assert result == mock_entry

    @pytest.mark.asyncio
    async def test_not_found(self, dao):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        dao.session_factory = MagicMock()
        dao.session_factory.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        dao.session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await dao.get_by_id(999)
        assert result is None


class TestGetAllConfigs:
    """测试查询所有配置."""

    @pytest.mark.asyncio
    async def test_returns_ordered_by_default(self, dao):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        entries = [MagicMock(), MagicMock()]
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = entries
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        dao.session_factory = MagicMock()
        dao.session_factory.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        dao.session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await dao.get_all_configs("u1")
        assert result == entries


class TestUpdateConfig:
    """测试更新配置."""

    @pytest.mark.asyncio
    async def test_update_with_dict(self, dao):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        dao.session_factory = MagicMock()
        dao.session_factory.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        dao.session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await dao.update_config(1, config={"target": "new"})
        assert result is True

    @pytest.mark.asyncio
    async def test_update_nothing_returns_false(self, dao):
        result = await dao.update_config(1)
        assert result is False


class TestDeleteConfig:
    """测试删除配置."""

    @pytest.mark.asyncio
    async def test_delete_existing(self, dao):
        mock_session = AsyncMock()
        mock_entry = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_entry)
        mock_session.delete = AsyncMock()
        mock_session.commit = AsyncMock()

        dao.session_factory = MagicMock()
        dao.session_factory.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        dao.session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await dao.delete_config(1)
        assert result is True
        mock_session.delete.assert_called_once_with(mock_entry)

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, dao):
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)

        dao.session_factory = MagicMock()
        dao.session_factory.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        dao.session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await dao.delete_config(999)
        assert result is False


class TestHealthCheck:
    """测试健康检查."""

    @pytest.mark.asyncio
    async def test_delegates_to_db_ops(self, dao):
        with patch.object(dao.db_ops, "health_check", return_value=True):
            result = await dao.health_check()
        assert result is True
