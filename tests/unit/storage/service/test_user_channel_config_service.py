"""UserChannelConfigService 单元测试.

测试用户渠道配置服务的业务逻辑: 获取/创建默认配置, 渠道配置管理.
Mock外部依赖: AsyncUserChannelConfigDAO.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.storage.service.user_channel_config_service import UserChannelConfigService


@pytest.fixture
def service(test_user):
    return UserChannelConfigService(
        session_factory=MagicMock(),
        user_id=test_user,
    )


class TestUpsertChannelConfig:
    """测试创建或更新渠道配置."""

    @pytest.mark.asyncio
    async def test_updates_existing(self, service):
        existing = MagicMock()
        existing.id = 1
        service.dao.get_config_by_type = AsyncMock(return_value=existing)
        service.dao.update_config = AsyncMock()
        service.dao.get_by_id = AsyncMock(return_value=existing)

        result = await service.upsert_channel_config(
            "wechat", {"target": "new_target"}, is_default=True
        )
        service.dao.update_config.assert_awaited_once_with(
            1, config={"target": "new_target"}, is_default=True,
        )
        assert result == existing

    @pytest.mark.asyncio
    async def test_creates_new_when_not_exist(self, service):
        mock_config = MagicMock()
        service.dao.get_config_by_type = AsyncMock(return_value=None)
        service.dao.create_config = AsyncMock(return_value=mock_config)

        result = await service.upsert_channel_config(
            "email", {"email_address": "test@example.com"}
        )
        service.dao.create_config.assert_awaited_once_with(
            user_id=service.user_id,
            channel_type="email",
            config={"email_address": "test@example.com"},
            is_default=False,
        )
        assert result == mock_config


class TestGetConfigForChannel:
    """测试获取渠道配置."""

    @pytest.mark.asyncio
    async def test_returns_config_dict_when_exists(self, service):
        mock_config = MagicMock()
        mock_config.get_config_dict.return_value = {"target": "test_channel"}
        service.dao.get_config_by_type = AsyncMock(return_value=mock_config)

        result = await service.get_config_for_channel("wechat")

        assert result == {"target": "test_channel"}

    @pytest.mark.asyncio
    async def test_returns_none_when_not_exists(self, service):
        service.dao.get_config_by_type = AsyncMock(return_value=None)

        result = await service.get_config_for_channel("email")

        assert result is None


class TestListConfigs:
    """测试列出渠道配置."""

    @pytest.mark.asyncio
    async def test_returns_all_configs(self, service):
        mock_configs = [MagicMock(), MagicMock()]
        service.dao.get_all_configs = AsyncMock(return_value=mock_configs)

        result = await service.list_configs()

        assert result == mock_configs
        service.dao.get_all_configs.assert_awaited_once_with(service.user_id)


class TestCheckServiceHealth:
    """测试渠道配置服务健康检查."""

    @pytest.mark.asyncio
    async def test_returns_healthy_when_db_ok(self, service):
        service.dao.health_check = AsyncMock(return_value=True)

        result = await service._check_service_health()

        assert result["status"] == "healthy"
        assert result["database_connected"] is True
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_returns_unhealthy_when_db_fails(self, service):
        service.dao.health_check = AsyncMock(return_value=False)

        result = await service._check_service_health()

        assert result["status"] == "unhealthy"
        assert result["database_connected"] is False

    @pytest.mark.asyncio
    async def test_returns_unhealthy_on_exception(self, service):
        service.dao.health_check = AsyncMock(
            side_effect=Exception("DB connection failed"),
        )

        result = await service._check_service_health()

        assert result["status"] == "unhealthy"
        assert result["database_connected"] is False
        assert "DB connection failed" in result["error"]
