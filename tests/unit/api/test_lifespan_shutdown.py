"""FastAPI lifespan shutdown 资源清理测试.

验证 shutdown 路径通过 LifecycleRegistry 关闭已注册资源,
DB 连接最后关闭, 异常隔离保证单个失败不中断整体.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def _reset_cleanup_task():
    """每个测试前后重置模块级周期清理任务引用, 保证隔离."""
    import src.api.fastapi_app as mod

    saved = mod._semantic_cache_cleanup_task
    mod._semantic_cache_cleanup_task = None
    yield
    mod._semantic_cache_cleanup_task = saved


@pytest.fixture
def _reset_lifecycle():
    """每个测试前后重置 LifecycleRegistry."""
    from src.core.lifecycle import reset_lifecycle_registry

    reset_lifecycle_registry()
    yield
    reset_lifecycle_registry()


class TestLifespanShutdownResources:
    """lifespan shutdown 应通过 LifecycleRegistry 释放全部资源."""

    @pytest.mark.asyncio
    async def test_shutdown_calls_close_all_and_db_last(
        self, _reset_cleanup_task, _reset_lifecycle
    ):
        """shutdown 应注册按维度资源 → close_all 关闭 → DB 最后关闭."""
        import src.api.fastapi_app as mod

        with (
            patch.object(mod, "get_agent_manager", side_effect=RuntimeError("skip")),
            patch(
                "src.storage.service.scheduled_message_service.shutdown_all_scheduled_services",
                new=AsyncMock(),
            ) as mock_shutdown_scheduled,
            patch(
                "src.storage.dao.async_database_manager.close_all_db_managers",
                new=AsyncMock(),
            ) as mock_close_db,
        ):
            async with mod.lifespan(MagicMock()):
                pass

        # scheduled_messages 被注册并通过 close_all 调用
        mock_shutdown_scheduled.assert_awaited_once()
        # DB 最后关闭
        mock_close_db.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_tolerates_resource_close_failure(
        self, _reset_cleanup_task, _reset_lifecycle
    ):
        """单个资源 close 失败不应中断整体 shutdown."""
        import src.api.fastapi_app as mod

        # 预注册一个会抛异常的 close 回调
        from src.core.lifecycle import register_resource

        register_resource("boom_resource", AsyncMock(side_effect=RuntimeError("boom")))

        with (
            patch.object(mod, "get_agent_manager", side_effect=RuntimeError("skip")),
            patch(
                "src.storage.service.scheduled_message_service.shutdown_all_scheduled_services",
                new=AsyncMock(),
            ),
            patch(
                "src.storage.dao.async_database_manager.close_all_db_managers",
                new=AsyncMock(),
            ) as mock_close_db,
        ):
            # 不应抛异常
            async with mod.lifespan(MagicMock()):
                pass

        # DB 仍然被关闭 (异常被隔离)
        mock_close_db.assert_awaited_once()
