#!/usr/bin/env python3
"""核心系统配置测试.

只保留验证真实配置行为的测试: YAML 覆盖、字段校验(非法值抛错).
纯 Pydantic 默认值/字段赋值回读的测试已删除.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.config.core_config import CacheConfig, CoreConfig


class TestCoreConfig:
    """核心配置主类测试."""

    @patch("src.config.core_config.get_module_config_sync")
    def test_core_config_yaml_override(self, mock_get_yaml_config) -> None:
        """YAML 配置应覆盖默认值."""
        mock_get_yaml_config.return_value = {
            "cache": {"enable_cache_stats": False},
        }
        config = CoreConfig.from_module_config()

        assert config.cache.enable_cache_stats is False


class TestCacheConfig:
    """缓存配置测试类."""

    def test_cache_config_size_validation(self) -> None:
        """缓存大小校验: 非正值应抛错."""
        for size in [1, 100, 500]:
            config = CacheConfig(pinned_memory_cache_size=size)
            assert config.pinned_memory_cache_size == size

        with pytest.raises(ValueError):
            CacheConfig(pinned_memory_cache_size=0)

        with pytest.raises(ValueError):
            CacheConfig(pinned_memory_cache_size=-1)


# 测试标记
pytestmark_unit = pytest.mark.unit
