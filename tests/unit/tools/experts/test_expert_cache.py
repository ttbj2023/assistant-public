"""ExpertCache单元测试 - 验证TTL缓存的核心逻辑.

测试范围:
1. 缓存键生成(make_key)
2. 搜索缓存的读写
3. 抓取缓存的读写
4. 地理缓存的读写
5. 缓存隔离性(三种缓存互不干扰)
6. 全局单例(get_expert_cache)
"""

from __future__ import annotations

import pytest

from src.tools.shared.cache import ExpertCache, get_expert_cache

# =============================================================================
# 1. 缓存键生成测试
# =============================================================================


class TestExpertCacheKeyGeneration:
    """测试ExpertCache.make_key缓存键生成"""

    def test_should_generate_consistent_key_for_same_input(self):
        key1 = ExpertCache.make_key("search", query="test", page=1)
        key2 = ExpertCache.make_key("search", query="test", page=1)
        assert key1 == key2

    def test_should_generate_different_key_for_different_prefix(self):
        key1 = ExpertCache.make_key("search", query="test")
        key2 = ExpertCache.make_key("fetch", query="test")
        assert key1 != key2

    def test_should_generate_different_key_for_different_kwargs(self):
        key1 = ExpertCache.make_key("search", query="hello")
        key2 = ExpertCache.make_key("search", query="world")
        assert key1 != key2

    def test_should_ignore_kwargs_order(self):
        key1 = ExpertCache.make_key("search", a="1", b="2")
        key2 = ExpertCache.make_key("search", b="2", a="1")
        assert key1 == key2

    def test_should_handle_empty_kwargs(self):
        key = ExpertCache.make_key("prefix")
        assert isinstance(key, str)
        assert len(key) == 32

    def test_should_handle_unicode_values(self):
        key = ExpertCache.make_key("search", query="中文查询测试")
        assert isinstance(key, str)
        assert len(key) == 32


# =============================================================================
# 2. 搜索缓存测试
# =============================================================================


class TestExpertCacheSearchCache:
    """测试搜索缓存的读写"""

    @pytest.mark.asyncio
    async def test_should_overwrite_existing_value(self):
        cache = ExpertCache()
        key = "test-key"
        await cache.set_search(key, "old")
        await cache.set_search(key, "new")
        result = await cache.get_search(key)
        assert result == "new"


# =============================================================================
# 3. 缓存隔离性测试
# =============================================================================


class TestExpertCacheIsolation:
    """测试三种缓存之间的隔离性"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "set_fn_a,get_fn_a,set_fn_b,get_fn_b,val_a,val_b",
        [
            (
                "set_search",
                "get_search",
                "set_fetch",
                "get_fetch",
                "search-value",
                "fetch-value",
            ),
            (
                "set_search",
                "get_search",
                "set_geo",
                "get_geo",
                "search-value",
                "geo-value",
            ),
            (
                "set_fetch",
                "get_fetch",
                "set_geo",
                "get_geo",
                "fetch-value",
                "geo-value",
            ),
        ],
    )
    async def test_should_isolate_cache_types(
        self, set_fn_a, get_fn_a, set_fn_b, get_fn_b, val_a, val_b
    ):
        cache = ExpertCache()
        key = "same-key"
        await getattr(cache, set_fn_a)(key, val_a)
        await getattr(cache, set_fn_b)(key, val_b)

        assert await getattr(cache, get_fn_a)(key) == val_a
        assert await getattr(cache, get_fn_b)(key) == val_b


# =============================================================================
# 4. 全局单例测试
# =============================================================================


class TestGetExpertCache:
    """测试get_expert_cache全局单例"""

    def test_should_return_expert_cache_instance(self):
        cache = get_expert_cache()
        assert isinstance(cache, ExpertCache)

    def test_should_return_same_instance(self):
        cache1 = get_expert_cache()
        cache2 = get_expert_cache()
        assert cache1 is cache2
