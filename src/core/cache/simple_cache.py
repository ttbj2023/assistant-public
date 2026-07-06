"""简化的缓存系统

使用Python标准库替代复杂的自定义缓存实现.
基于functools.lru_cache和cachetools的标准解决方案.
"""

from __future__ import annotations

import logging
from typing import Any, ParamSpec, Protocol, TypeVar

# 标准缓存库
from cachetools import LRUCache


def build_simple_cache_key(
    prefix: str,
    model_id: str,
    agent_config: dict | None = None,
) -> str:
    """构建LLM客户端缓存键.

    缓存键基于 model_id 和 agent_config 中的构造级参数(如 num_ctx).
    相同模型但不同构造参数会生成不同缓存键, 避免不同 Agent 共用错误配置的客户端.
    streaming 属于调用级参数, 不参与缓存键.

    Args:
        prefix: 缓存前缀(如 "llm" 或 "embedding")
        model_id: 模型ID
        agent_config: Agent配置(可选, 其中的构造级参数参与缓存键)

    Returns:
        缓存键字符串

    """
    key = f"client:{prefix}:{model_id}"
    if agent_config:
        construction_params = {
            k: v for k, v in agent_config.items() if k not in ("streaming", "model")
        }
        if construction_params:
            # 排序保证字典顺序不影响 key 稳定性
            params_repr = ",".join(
                f"{k}={v}" for k, v in sorted(construction_params.items())
            )
            key = f"{key}:{params_repr}"
    return key


logger = logging.getLogger(__name__)

# 使用ParamSpec来更好地处理泛型
P = ParamSpec("P")
R = TypeVar("R")


# 定义协议以替代Any类型
class LLMClientProtocol(Protocol):
    """LLM客户端协议, 支持 LangChain BaseChatModel 接口."""

    def invoke(self, input: Any, **kwargs: Any) -> Any: ...

    async def ainvoke(self, input: Any, **kwargs: Any) -> Any: ...


class EmbeddingsClientProtocol(Protocol):
    """嵌入模型客户端协议, 支持 LangChain Embeddings 接口."""

    def embed_query(self, text: str, **kwargs: Any) -> list[float]: ...

    def embed_documents(self, texts: list[str], **kwargs: Any) -> list[list[float]]: ...


class ToolProtocol(Protocol):
    """工具协议, 支持 LangChain BaseTool 接口."""

    def _run(self, *args: Any, **kwargs: Any) -> Any: ...

    async def _arun(self, *args: Any, **kwargs: Any) -> Any: ...


T = TypeVar("T")
K = TypeVar("K")


class SimpleMemoryCache:
    """简单的内存缓存.

    使用cachetools.LRUCache替代复杂的自定义实现.
    符合Python标准库最佳实践,性能更优.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        """初始化简单缓存.

        Args:
            maxsize: 最大缓存条目数

        """
        self._cache: LRUCache = LRUCache(maxsize=maxsize)

        # 统计信息
        self._hits = 0
        self._misses = 0
        self._sets = 0

        # 检查是否启用统计功能
        try:
            from src.config.core_config import get_config

            core_config = get_config()
            self._stats_enabled = core_config.cache.enable_cache_stats
        except Exception as e:
            logger.debug("缓存统计配置获取失败, 默认启用: %s", e)
            self._stats_enabled = True  # 默认启用

    def get(self, key: K) -> T | None:
        """获取缓存值.

        Args:
            key: 缓存键

        Returns:
            缓存值或None

        """
        # 检查缓存是否存在
        value = self._cache.get(key, None)

        # 更新统计信息
        if self._stats_enabled:
            if value is not None:
                self._hits += 1
            else:
                self._misses += 1

        return value

    def set(self, key: K, value: T) -> None:
        """设置缓存值.

        Args:
            key: 缓存键
            value: 缓存值

        """
        self._cache[key] = value

        # 更新统计信息
        if self._stats_enabled:
            self._sets += 1

    def clear(self) -> None:
        """清空缓存."""
        self._cache.clear()

        # 重置统计信息
        if self._stats_enabled:
            self._hits = 0
            self._misses = 0
            self._sets = 0

    def size(self) -> int:
        """获取缓存大小.

        Returns:
            缓存条目数

        """
        return len(self._cache)

    def get_cache_stats(self) -> dict[str, int | float | bool]:
        """获取缓存统计信息.

        Returns:
            统计信息字典

        """
        total_requests = self._hits + self._misses
        hit_rate = self._hits / total_requests if total_requests > 0 else 0.0

        return {
            "hits": self._hits,
            "misses": self._misses,
            "sets": self._sets,
            "hit_rate": hit_rate,
            "total_requests": total_requests,
            "current_size": len(self._cache),
            "max_size": self._cache.maxsize,
            "utilization": len(self._cache) / self._cache.maxsize
            if self._cache.maxsize > 0
            else 0,
            "stats_enabled": self._stats_enabled,
        }

    # Client manager 兼容方法
    def get_llm_client(
        self,
        model_id: str,
        agent_config: dict | None = None,
    ) -> LLMClientProtocol | None:
        """获取LLM客户端(兼容性方法).

        Args:
            model_id: 模型ID
            agent_config: Agent配置

        Returns:
            缓存的客户端或None

        """
        cache_key = build_simple_cache_key("llm", model_id, agent_config)
        return self.get(cache_key)

    def cache_llm_client(
        self,
        model_id: str,
        client: LLMClientProtocol,
        agent_config: dict | None = None,
    ) -> None:
        """缓存LLM客户端(兼容性方法).

        Args:
            model_id: 模型ID
            client: 客户端实例
            agent_config: Agent配置

        """
        cache_key = build_simple_cache_key("llm", model_id, agent_config)
        self.set(cache_key, client)

    def get_embedding_client(self, model_id: str) -> EmbeddingsClientProtocol | None:
        """获取嵌入模型客户端(兼容性方法).

        Args:
            model_id: 模型ID

        Returns:
            缓存的客户端或None

        """
        cache_key = build_simple_cache_key("embedding", model_id)
        return self.get(cache_key)

    def cache_embedding_client(
        self,
        model_id: str,
        client: EmbeddingsClientProtocol,
    ) -> None:
        """缓存嵌入模型客户端(兼容性方法).

        Args:
            model_id: 模型ID
            client: 客户端实例

        """
        cache_key = build_simple_cache_key("embedding", model_id)
        self.set(cache_key, client)

    def get_stats(self) -> dict[str, Any]:
        """获取缓存统计信息(兼容性方法).

        Returns:
            统计信息字典

        """
        # 使用新的统计功能
        cache_stats = self.get_cache_stats()

        return {
            "llm_clients": {"hit_rate": cache_stats["hit_rate"]},
            "embedding_clients": {"hit_rate": cache_stats["hit_rate"]},
            "total_hit_rate": cache_stats["hit_rate"],
            "detailed_stats": cache_stats,  # 添加详细统计信息
        }

    def get_client_count(self) -> dict[str, int]:
        """获取客户端数量统计(兼容性方法).

        Returns:
            客户端数量字典

        """
        return {
            "total_clients": self.size(),
            "llm_clients": 0,
            "embedding_clients": 0,
        }

    def clear_all_clients(self) -> None:
        """清空所有客户端(兼容性方法)."""
        self.clear()


# 全局缓存实例
_global_caches: dict[str, SimpleMemoryCache] = {}


def get_cache(name: str, maxsize: int = 1000) -> SimpleMemoryCache:
    """获取命名缓存实例.

    Args:
        name: 缓存名称
        maxsize: 最大缓存条目数

    Returns:
        缓存实例

    """
    if name not in _global_caches:
        _global_caches[name] = SimpleMemoryCache(maxsize=maxsize)
    return _global_caches[name]


def get_client_cache() -> SimpleMemoryCache:
    """获取客户端缓存实例."""
    return get_cache("client", maxsize=100)


def get_token_cache() -> SimpleMemoryCache:
    """获取Token缓存实例."""
    return get_cache("token", maxsize=1000)


def is_token_cache_enabled() -> bool:
    """Token缓存是否启用."""
    return get_cache("token", maxsize=1000) is not None


__all__ = [
    "SimpleMemoryCache",
    "get_cache",
    "get_client_cache",
    "get_token_cache",
    "is_token_cache_enabled",
]
