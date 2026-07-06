"""语义缓存周期清理协程单元测试.

验证 _periodic_semantic_cache_cleanup 的核心行为:
1. sleep 后调用 cleanup
2. cleanup 异常不中断循环
3. cancel 正确退出

Mock策略: 替换 asyncio.sleep 加速循环, mock SemanticCache 避免真实 ChromaDB.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestPeriodicSemanticCacheCleanup:
    """测试 _periodic_semantic_cache_cleanup 周期清理."""

    @pytest.mark.asyncio
    async def test_should_call_cleanup_after_sleep(self):
        """sleep 后应调用 cleanup."""
        from src.api.fastapi_app import _periodic_semantic_cache_cleanup

        mock_cache = MagicMock()
        mock_cache.cleanup = AsyncMock(return_value=5)

        sleep_count = 0

        async def fake_sleep(_interval: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError

        with (
            patch.object(asyncio, "sleep", fake_sleep),
            patch(
                "src.tools.shared.semantic_cache.get_semantic_cache",
                return_value=mock_cache,
            ),
        ):
            with contextlib.suppress(asyncio.CancelledError):
                await _periodic_semantic_cache_cleanup()

        mock_cache.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_should_continue_after_cleanup_exception(self):
        """cleanup 异常不应中断循环."""
        from src.api.fastapi_app import _periodic_semantic_cache_cleanup

        mock_cache = MagicMock()
        mock_cache.cleanup = AsyncMock(side_effect=[Exception("boom"), 3])

        sleep_count = 0

        async def fake_sleep(_interval: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise asyncio.CancelledError

        with (
            patch.object(asyncio, "sleep", fake_sleep),
            patch(
                "src.tools.shared.semantic_cache.get_semantic_cache",
                return_value=mock_cache,
            ),
        ):
            with contextlib.suppress(asyncio.CancelledError):
                await _periodic_semantic_cache_cleanup()

        # 第一次 cleanup 抛异常(被吞), 第二次成功, 两次都被调用
        assert mock_cache.cleanup.await_count == 2
