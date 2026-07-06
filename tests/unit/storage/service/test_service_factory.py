"""Service 工厂创建行为与分支逻辑单元测试.

验证工厂每次调用创建独立实例, 以及 create_retrieval_service 的
config 分支. Mock 底层 DB manager 创建和 Service 类, 避免真实
DB engine, 可并发执行.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_service_cache() -> Iterator[None]:
    """每个测试前后清空全局 Service 缓存, 隔离测试间状态."""
    from src.storage.service.service_factory import clear_vector_cache

    clear_vector_cache()
    yield
    clear_vector_cache()


@pytest.fixture
def _mocked_conversation_construction() -> Iterator[list[MagicMock]]:
    """Mock create_conversation_service 底层构造, 返回已创建实例列表供断言计数."""
    created: list[MagicMock] = []

    async def fake_db_manager(*args: object, **kwargs: object) -> MagicMock:
        m = MagicMock()
        m.session_factory = MagicMock()
        return m

    def fake_service_cls(session_factory: object) -> MagicMock:
        instance = MagicMock(name="ConversationService")
        instance.session_factory = session_factory
        created.append(instance)
        return instance

    with (
        patch(
            "src.storage.dao.async_database_manager.create_async_conversation_history_db_manager",
            new=AsyncMock(side_effect=fake_db_manager),
        ),
        patch(
            "src.storage.service.conversation_service.ConversationService",
            side_effect=fake_service_cls,
        ),
    ):
        yield created


class TestServiceFactoryCreation:
    """Service 工厂每次调用创建独立实例."""

    @pytest.mark.asyncio
    async def test_different_params_creates_separate_instances(
        self,
        _mocked_conversation_construction: list[MagicMock],
    ) -> None:
        """不同参数创建独立实例, 每次调用都构造新 Service."""
        from src.storage.service.service_factory import create_conversation_service

        svc1 = await create_conversation_service("u1", "t1", agent_id="a1")
        svc2 = await create_conversation_service("u2", "t1", agent_id="a1")
        svc3 = await create_conversation_service("u1", "t2", agent_id="a1")
        svc4 = await create_conversation_service("u1", "t1", agent_id="a2")

        assert len({id(svc1), id(svc2), id(svc3), id(svc4)}) == 4
        assert len(_mocked_conversation_construction) == 4


class TestRetrievalServiceFactoryBranch:
    """create_retrieval_service 的 config 分支逻辑."""

    @pytest.mark.asyncio
    async def test_embeddings_disabled_enters_pure_sql_mode(self) -> None:
        """inference.embeddings.enabled=False → vector_service=None, 纯 SQL 模式.

        协作场景: create_retrieval_service 读取 inference 配置 → 决定是否创建向量服务
        Mock 边界: Mock create_conversation_service (避免真实 DB) + get_config (注入禁用配置)
        验证重点: 嵌入禁用分支 → vector_service=None / enable_vector_search=False / SQL 可用
        业务价值: 支持无 GPU 环境降级运行, 纯 SQL 检索仍可用
        """
        from src.storage.service.service_factory import create_retrieval_service

        mock_inference = MagicMock()
        mock_inference.embeddings.enabled = False
        mock_conv_service = MagicMock(name="conv_service")

        with (
            patch(
                "src.storage.service.service_factory.create_conversation_service",
                new=AsyncMock(return_value=mock_conv_service),
            ),
            patch(
                "src.config.inference_config.get_config",
                return_value=mock_inference,
            ),
        ):
            svc = await create_retrieval_service("u1", "t1", agent_id="a1")

        assert svc.enable_vector_search is False
        assert svc.vector_service is None
        assert svc.enable_sql_search is True
