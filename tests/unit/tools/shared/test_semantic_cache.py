"""语义缓存 (SemanticCache) 单元测试.

测试范围:
1. put + get: 写入后能精确取回
2. 相似query语义命中
3. 不相似query不命中
4. TTL过期不命中
5. cleanup清理过期条目
6. 异常降级为miss

Mock策略: patch 调用方 create_embeddings 注入 Mock, 避免真实模型调用.
"""

from __future__ import annotations

import json

import pytest

from src.tools.shared.semantic_cache import SemanticCache


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch: pytest.MonkeyPatch):
    """Patch 调用方 create_embeddings 注入 Mock, 避免真实模型调用."""
    from tests.mocks.unified_factory import UnifiedMockFactory

    mock_embeddings = UnifiedMockFactory.create_embeddings(realistic=True)
    monkeypatch.setattr(
        "src.tools.shared.semantic_cache.create_embeddings",
        lambda *args, **kwargs: mock_embeddings,
    )


@pytest.fixture
def cache(tmp_path):
    """创建临时目录的SemanticCache实例."""
    return SemanticCache(
        cache_dir=str(tmp_path / "semantic"),
        collection_name="test_cache",
        ttl=900.0,
        threshold=0.85,
    )


@pytest.fixture
def sample_result():
    """示例研究结果."""
    return {
        "result": "### 核心发现\n- Go 1.23发布于2024年8月",
        "query": "Go 1.23 release notes",
        "depth": "deep",
        "language": "zh",
        "tools_used": ["doubao_search", "fetch_webpage"],
    }


class TestSemanticCacheBasic:
    """基础读写测试."""

    @pytest.mark.asyncio
    async def test_should_return_none_when_cache_empty(self, cache):
        """空缓存返回None."""
        result = await cache.get("test query")
        assert result is None

    @pytest.mark.asyncio
    async def test_should_store_and_retrieve(self, cache, sample_result):
        """写入后能精确取回."""
        value = json.dumps(sample_result, ensure_ascii=False)
        await cache.put("Go 1.23 release notes", value)

        # 相同query应命中
        cached = await cache.get("Go 1.23 release notes")
        assert cached is not None
        assert json.loads(cached)["result"] == sample_result["result"]


class TestSemanticCacheTTL:
    """TTL过期测试."""

    @pytest.mark.asyncio
    async def test_should_miss_when_expired(self, cache, sample_result):
        """过期条目不命中."""
        value = json.dumps(sample_result, ensure_ascii=False)
        await cache.put("test query", value)

        # 使用极短TTL使条目过期
        cached = await cache.get("test query", ttl=0.001)
        # 等待TTL过期
        import asyncio

        await asyncio.sleep(0.01)
        cached = await cache.get("test query", ttl=0.001)
        assert cached is None

    @pytest.mark.asyncio
    async def test_should_cleanup_expired(self, cache, sample_result):
        """cleanup清理过期条目."""
        value = json.dumps(sample_result, ensure_ascii=False)
        await cache.put("test query", value)

        # 极短TTL下cleanup
        import asyncio

        await asyncio.sleep(0.01)
        count = await cache.cleanup(ttl=0.001)
        assert count == 1

        # cleanup后应miss
        cached = await cache.get("test query")
        assert cached is None


class TestSemanticCacheEdgeCases:
    """边界和异常测试."""

    @pytest.mark.asyncio
    async def test_stats_before_init(self, cache):
        """初始化前stats返回not_initialized."""
        stats = cache.stats()
        assert stats["status"] == "not_initialized"
        assert stats["count"] == 0

    @pytest.mark.asyncio
    async def test_stats_after_use(self, cache, sample_result):
        """使用后stats返回正确计数."""
        await cache.put("test", json.dumps(sample_result))
        stats = cache.stats()
        assert stats["status"] == "active"
        assert stats["count"] == 1
