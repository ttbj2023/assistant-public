"""LifecycleRegistry 单元测试."""

from __future__ import annotations

import pytest

from src.core.lifecycle import (
    get_lifecycle_registry,
    reset_lifecycle_registry,
)


class TestLifecycleRegistry:
    """LifecycleRegistry 注册与关闭行为."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        reset_lifecycle_registry()
        yield
        reset_lifecycle_registry()

    @pytest.mark.asyncio
    async def test_close_all_executes_in_reverse_order(self):
        """close_all 按注册逆序执行."""
        calls = []
        reg = get_lifecycle_registry()
        reg.register("first", lambda: calls.append("first"))
        reg.register("second", lambda: calls.append("second"))
        reg.register("third", lambda: calls.append("third"))

        await reg.close_all()

        assert calls == ["third", "second", "first"]

    @pytest.mark.asyncio
    async def test_close_all_supports_async_callbacks(self):
        """close_all 支持 async 回调."""

        calls = []

        async def async_close():
            calls.append("async")

        reg = get_lifecycle_registry()
        reg.register("async_resource", async_close)

        await reg.close_all()

        assert calls == ["async"]

    @pytest.mark.asyncio
    async def test_close_all_tolerates_exceptions(self):
        """单个回调失败不中断后续."""
        calls = []

        def boom():
            raise RuntimeError("boom")

        reg = get_lifecycle_registry()
        reg.register("boom", boom)
        reg.register("after", lambda: calls.append("after"))

        await reg.close_all()

        assert calls == ["after"]

    @pytest.mark.asyncio
    async def test_close_all_clears_registry(self):
        """close_all 后注册表清空."""
        reg = get_lifecycle_registry()
        reg.register("resource", lambda: None)

        await reg.close_all()

        assert len(reg._closers) == 0
        assert len(reg._order) == 0

    def test_register_is_idempotent(self):
        """重复注册同名资源更新回调但不改变顺序."""
        reg = get_lifecycle_registry()
        reg.register("resource", lambda: 1)
        reg.register("resource", lambda: 2)

        assert len(reg._order) == 1
