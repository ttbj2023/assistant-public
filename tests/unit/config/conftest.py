"""配置测试公共 fixture."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """每个测试前后重置配置模块缓存, 保证测试隔离.

    同时清理 Pydantic 实例缓存(reset_config_cache)和 YAML 字典缓存(clear_cache),
    避免上一个测试的 config.yaml 解析结果泄漏到下一个测试.
    """
    from src.config import clear_cache, reset_config_cache

    clear_cache()
    reset_config_cache()
    yield
    reset_config_cache()
