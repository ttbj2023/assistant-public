"""简化的缓存系统包.

基于cachetools的标准LRU缓存解决方案.
"""

from __future__ import annotations

from .simple_cache import (
    SimpleMemoryCache,
    get_cache,
    get_client_cache,
    get_token_cache,
    is_token_cache_enabled,
)

__all__ = [
    "SimpleMemoryCache",
    "get_cache",
    "get_client_cache",
    "get_token_cache",
    "is_token_cache_enabled",
]
