"""缓存系统单元测试 - v2.0 规范化版本

遵循项目测试规范，验证缓存系统的核心功能、配置管理和性能特性。
测试层级：单元测试（白盒测试）- 验证单一模块的业务逻辑正确性。

测试范围：
- SimpleMemoryCache核心功能和统计
- 缓存键生成和验证
- 配置管理集成
- 内存泄漏防护
- TTL和LRU机制

Mock策略：只Mock外部依赖，保留缓存系统的真实业务逻辑。
"""

import pytest

from src.core.cache.simple_cache import (
    SimpleMemoryCache,
    build_simple_cache_key,
)


class TestSimpleMemoryCache:
    """SimpleMemoryCache核心功能测试类"""

    @pytest.fixture
    def cache(self):
        """创建测试用的缓存实例"""
        return SimpleMemoryCache(maxsize=10)

    @pytest.fixture
    def small_cache(self):
        """创建小容量缓存用于LRU测试"""
        return SimpleMemoryCache(maxsize=3)

    @pytest.fixture
    def cache_with_config(self):
        """创建带有测试配置的缓存实例"""
        return SimpleMemoryCache(maxsize=5)

    # ===== 基础功能测试 =====

    @pytest.mark.unit
    def test_cache_should_store_and_retrieve_data(self, cache) -> None:
        """测试缓存应该能够存储和检索数据"""
        # Arrange
        key, value = "test_key", "test_value"

        # Act
        cache.set(key, value)
        result = cache.get(key)

        # Assert
        assert result == value
        assert cache.size() == 1

    @pytest.mark.unit
    def test_cache_should_return_none_for_nonexistent_key(self, cache) -> None:
        """测试缓存对不存在的键应该返回None"""
        # Act & Assert
        assert cache.get("nonexistent_key") is None

    @pytest.mark.unit
    def test_cache_should_update_existing_key(self, cache) -> None:
        """测试缓存应该能够更新现有键的值"""
        # Arrange
        key = "test_key"
        cache.set(key, "old_value")

        # Act
        cache.set(key, "new_value")
        result = cache.get(key)

        # Assert
        assert result == "new_value"
        assert cache.size() == 1

    # ===== LRU淘汰机制测试 =====

    @pytest.mark.unit
    def test_cache_should_evict_lru_when_full(self, small_cache) -> None:
        """测试缓存满时应该淘汰最久未使用的数据"""
        # Arrange
        small_cache.set("key1", "value1")
        small_cache.set("key2", "value2")
        small_cache.set("key3", "value3")

        # Act - 访问key1使其变为最近使用
        small_cache.get("key1")
        small_cache.set("key4", "value4")  # 应该淘汰key2

        # Assert
        assert small_cache.get("key1") == "value1"  # 仍然存在
        assert small_cache.get("key2") is None  # 被淘汰
        assert small_cache.get("key3") == "value3"  # 仍然存在
        assert small_cache.get("key4") == "value4"  # 新添加
        assert small_cache.size() == 3

    @pytest.mark.unit
    def test_cache_should_handle_serial_access_pattern(self, small_cache) -> None:
        """测试缓存应该正确处理序列访问模式"""
        # Act - 按序列添加多个项目
        for i in range(5):
            small_cache.set(f"key{i}", f"value{i}")

        # Assert - 应该保留最后3个项目（maxsize=3）
        assert small_cache.get("key0") is None  # 已淘汰
        assert small_cache.get("key1") is None  # 已淘汰
        assert small_cache.get("key2") == "value2"  # 保留
        assert small_cache.get("key3") == "value3"  # 保留
        assert small_cache.get("key4") == "value4"  # 保留
        assert small_cache.size() == 3  # key2, key3, key4

    # ===== 清理功能测试 =====

    @pytest.mark.unit
    def test_cache_clear_should_remove_all_data(self, cache) -> None:
        """测试缓存清理应该移除所有数据"""
        # Arrange
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        assert cache.size() == 2

        # Act
        cache.clear()

        # Assert
        assert cache.size() == 0
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    @pytest.mark.unit
    def test_cache_clear_should_reset_statistics(self, cache_with_config) -> None:
        """测试缓存清理应该重置统计信息"""
        # Arrange
        cache_with_config.set("key1", "value1")
        cache_with_config.get("key1")  # 命中
        cache_with_config.get("nonexistent")  # 未命中

        # Act
        cache_with_config.clear()

        # Assert
        stats = cache_with_config.get_cache_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["sets"] == 0

    # ===== 统计功能测试 =====

    @pytest.mark.unit
    def test_cache_should_track_hit_rate_correctly(self, cache_with_config) -> None:
        """测试缓存应该正确跟踪命中率"""
        # Arrange
        cache_with_config.set("key1", "value1")
        cache_with_config.set("key2", "value2")

        # Act - 执行多次访问
        cache_with_config.get("key1")  # hit
        cache_with_config.get("key1")  # hit
        cache_with_config.get("key2")  # hit
        cache_with_config.get("nonexistent")  # miss

        # Assert
        stats = cache_with_config.get_cache_stats()
        assert stats["hits"] == 3
        assert stats["misses"] == 1
        assert stats["sets"] == 2
        assert stats["hit_rate"] == 0.75  # 3/4 = 0.75

    @pytest.mark.unit
    def test_cache_should_utilization_calculation(self, cache) -> None:
        """测试缓存应该正确计算利用率"""
        # Arrange & Act
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        # Assert
        stats = cache.get_cache_stats()
        assert stats["current_size"] == 2
        assert stats["max_size"] == 10
        assert stats["utilization"] == 0.2  # 2/10 = 0.2

    # ===== 边界条件测试 =====

    @pytest.mark.unit
    def test_cache_should_handle_maxsize_one(self) -> None:
        """测试缓存应该能处理maxsize为1的情况"""
        # Arrange
        cache = SimpleMemoryCache(maxsize=1)

        # Act
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        # Assert
        assert cache.get("key1") is None
        assert cache.get("key2") == "value2"
        assert cache.size() == 1


class TestCacheKeyGeneration:
    """缓存键生成测试类"""

    @pytest.mark.unit
    def test_build_simple_cache_key_with_agent_config(self) -> None:
        """测试包含Agent配置的缓存键生成 - 构造级参数参与缓存键"""
        agent_config = {"temperature": 0.7, "max_tokens": 1000}

        key = build_simple_cache_key("llm", "gpt-4", agent_config)

        assert key == "client:llm:gpt-4:max_tokens=1000,temperature=0.7"

    @pytest.mark.unit
    def test_build_simple_cache_key_ignores_streaming(self) -> None:
        """streaming 属于调用级参数, 不参与缓存键"""
        key_with_streaming = build_simple_cache_key("llm", "gpt-4", {"streaming": True})
        key_without_streaming = build_simple_cache_key("llm", "gpt-4")

        assert key_with_streaming == key_without_streaming
        assert key_with_streaming == "client:llm:gpt-4"

    @pytest.mark.unit
    def test_build_simple_cache_key_with_different_models(self) -> None:
        """测试不同模型生成不同的缓存键"""
        key1 = build_simple_cache_key("llm", "gpt-4")
        key2 = build_simple_cache_key("llm", "gpt-3.5")
        key3 = build_simple_cache_key("embedding", "gpt-4")

        assert key1 != key2
        assert key1 != key3
