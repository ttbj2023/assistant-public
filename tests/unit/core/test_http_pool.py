"""HttpPool 单元测试.

测试职责: 验证 HTTP 连接池的核心功能逻辑
测试范围: AsyncClient 创建/复用/关闭
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.core.http_pool import HttpPool


class TestHttpPool:
    """测试 HttpPool 核心功能."""

    @pytest.fixture
    def http_pool(self):
        """创建 HTTP 连接池实例."""
        return HttpPool()

    def test_get_should_create_new_client_on_first_call(self, http_pool):
        """首次调用应创建新客户端."""
        with patch("src.core.http_pool.httpx.AsyncClient") as mock_client_class:
            mock_instance = Mock()
            mock_client_class.return_value = mock_instance

            result = http_pool.get("test_provider")

            assert result is mock_instance
            mock_client_class.assert_called_once()
            assert "test_provider" in http_pool._clients

    def test_get_should_reuse_existing_client(self, http_pool):
        """同 provider 第二次调用应复用客户端."""
        with patch("src.core.http_pool.httpx.AsyncClient") as mock_client_class:
            mock_instance = Mock()
            mock_client_class.return_value = mock_instance

            client1 = http_pool.get("test_provider")
            client2 = http_pool.get("test_provider")

            assert client1 is client2
            assert mock_client_class.call_count == 1

    def test_get_different_providers_should_create_different_clients(self, http_pool):
        """不同 provider 应创建不同客户端."""
        with patch("src.core.http_pool.httpx.AsyncClient") as mock_client_class:
            mock_client_class.return_value = Mock()

            http_pool.get("provider1")
            http_pool.get("provider2")

            assert mock_client_class.call_count == 2
            assert "provider1" in http_pool._clients
            assert "provider2" in http_pool._clients

    @pytest.mark.asyncio
    async def test_close_all_should_clear_all_clients(self, http_pool):
        """close_all 应清空所有客户端."""
        with patch("src.core.http_pool.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            http_pool.get("provider1")
            http_pool.get("provider2")

            assert len(http_pool._clients) == 2

            await http_pool.close_all()

            assert mock_client.aclose.call_count == 2
            assert len(http_pool._clients) == 0
