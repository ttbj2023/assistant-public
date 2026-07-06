"""专家工具缓存 - 基于cachetools.TTLCache的轻量级缓存."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from cachetools import TTLCache

logger = logging.getLogger(__name__)


class ExpertCache:
    """专家工具共享缓存.

    为搜索结果,网页抓取等提供TTL缓存, 避免重复请求外部资源.
    """

    def __init__(
        self,
        max_entries: int = 1000,
        search_ttl: float = 900.0,
        fetch_ttl: float = 1800.0,
        geo_ttl: float = 600.0,
    ) -> None:
        self._search_cache: TTLCache = TTLCache(maxsize=max_entries, ttl=search_ttl)
        self._fetch_cache: TTLCache = TTLCache(maxsize=max_entries, ttl=fetch_ttl)
        self._geo_cache: TTLCache = TTLCache(maxsize=max_entries, ttl=geo_ttl)

    @staticmethod
    def make_key(prefix: str, **kwargs: Any) -> str:
        """生成缓存键."""
        raw = f"{prefix}:{json.dumps(kwargs, sort_keys=True, ensure_ascii=False)}"
        return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()

    async def get_search(self, key: str) -> str | None:
        """获取搜索缓存."""
        return self._search_cache.get(key)

    async def set_search(self, key: str, value: str) -> None:
        """设置搜索缓存."""
        self._search_cache[key] = value

    async def get_fetch(self, key: str) -> str | None:
        """获取抓取缓存."""
        return self._fetch_cache.get(key)

    async def set_fetch(self, key: str, value: str) -> None:
        """设置抓取缓存."""
        self._fetch_cache[key] = value

    async def get_geo(self, key: str) -> str | None:
        """获取地理缓存."""
        return self._geo_cache.get(key)

    async def set_geo(self, key: str, value: str) -> None:
        """设置地理缓存."""
        self._geo_cache[key] = value


_expert_cache: ExpertCache | None = None


def get_expert_cache() -> ExpertCache:
    """获取全局专家缓存实例."""
    global _expert_cache
    if _expert_cache is None:
        _expert_cache = ExpertCache()
    return _expert_cache
