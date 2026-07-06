"""Service 工厂组合装配集成测试.

验证 create_conversation_data_service 正确编排 4 个子 Service.
子 Service 底层 Engine 通过 _db_manager_cache 全局复用, 保证 DB 层状态一致.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _isolate_vector_cache() -> Iterator[None]:
    """每个测试前后清空向量服务缓存, 隔离测试间状态."""
    from src.storage.service.service_factory import clear_vector_cache

    clear_vector_cache()
    yield
    clear_vector_cache()


@pytest.mark.integration
@pytest.mark.serial
class TestServiceFactoryCompositionIntegration:
    """Service 工厂组合装配集成测试."""

    @pytest.mark.asyncio
    async def test_integration_conversation_data_service_composes_sub_services(
        self,
    ) -> None:
        """create_conversation_data_service 正确组合 4 个子 Service.

        协作场景: create_conversation_data_service → 编排 create_conversation_service
            + create_memory_service + create_todo_service + create_vector_service
        Mock 边界: 无 Mock, 使用真实 Service 实例 + 真实 SQLite
        验证重点: data_svc 持有 4 个正确类型的子 Service, 底层 Engine 通过
            _db_manager_cache 全局复用保证 DB 层状态一致
        """
        from src.storage.service.service_factory import (
            create_conversation_data_service,
        )

        data_svc = await create_conversation_data_service(
            "cds_test", "main", agent_id="test-agent"
        )

        assert data_svc.conversation_service is not None
        assert data_svc.memory_service is not None
        assert data_svc.todo_service is not None
        assert data_svc.vector_service is not None
