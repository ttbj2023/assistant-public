"""语义缓存 - 基于ChromaDB + Embedding的向量相似度缓存.

为深度研究等高耗时操作提供语义级缓存:
- 不同表述的同一问题可命中同一缓存条目
- 支持中英文混合查询的语义匹配
- 基于元数据时间戳的TTL过期机制
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from src.inference.embeddings.embeddings import create_embeddings

logger = logging.getLogger(__name__)

# 默认配置
_DEFAULT_COLLECTION = "research_cache"
_DEFAULT_TTL = 14400.0  # 4小时 (研究型结果跨用户共享, 时效性较长)
_DEFAULT_THRESHOLD = 0.85
_DEFAULT_CACHE_DIR = "data/.cache/semantic"
_DEFAULT_CLEANUP_INTERVAL = 3600.0  # 周期清理间隔(秒), TTL(4h)的1/4


class SemanticCache:
    """基于向量相似度的语义缓存.

    使用ChromaDB存储query embedding和对应的缓存结果,
    通过近似最近邻搜索实现语义级缓存命中.

    特性:
    - 延迟初始化: ChromaDB和embedding在首次调用时创建
    - 全局共享: 研究结果不按用户隔离
    - TTL过期: 通过元数据时间戳实现, 查询时自动过滤
    """

    def __init__(
        self,
        *,
        cache_dir: str = _DEFAULT_CACHE_DIR,
        collection_name: str = _DEFAULT_COLLECTION,
        ttl: float = _DEFAULT_TTL,
        threshold: float = _DEFAULT_THRESHOLD,
    ) -> None:
        self._cache_dir = cache_dir
        self._collection_name = collection_name
        self._ttl = ttl
        self._threshold = threshold

        self._client: Any = None
        self._collection: Any = None
        self._embeddings: Any = None
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """延迟初始化ChromaDB客户端和embedding模型."""
        if self._initialized:
            return

        from pathlib import Path

        import chromadb

        cache_path = Path(self._cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 缓存初始化, 一次性

        self._client = chromadb.PersistentClient(path=str(cache_path))
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        # 复用项目统一embedding基础设施
        from src.config.inference_config import get_config

        inference_config = get_config()
        model_id = inference_config.embeddings.model
        self._embeddings = create_embeddings(model=model_id)

        self._initialized = True
        logger.info(
            "语义缓存已初始化: collection=%s, dir=%s",
            self._collection_name,
            self._cache_dir,
        )

    async def _embed_query(self, text: str) -> list[float]:
        """生成文本embedding, 兼容同步/异步embedding接口."""
        import inspect

        result = self._embeddings.aembed_query(text)
        # Mock embedding可能返回list[float]而非协程
        if inspect.isawaitable(result):
            return await result
        return result

    async def get(
        self,
        query: str,
        *,
        threshold: float | None = None,
        ttl: float | None = None,
    ) -> str | None:
        """语义检索缓存.

        Args:
            query: 查询文本
            threshold: 相似度阈值, 默认使用实例配置
            ttl: 缓存TTL(秒), 默认使用实例配置

        Returns:
            缓存的JSON字符串, 未命中返回None
        """
        threshold = threshold or self._threshold
        ttl = ttl or self._ttl

        try:
            await self._ensure_initialized()

            # 生成query embedding
            query_embedding = await self._embed_query(query)

            # 构建TTL过滤条件
            min_cached_at = time.time() - ttl
            where_filter = {"cached_at": {"$gte": min_cached_at}}

            # ChromaDB相似度搜索
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=1,
                where=where_filter,
                include=["documents", "distances", "metadatas"],
            )

            if not results["documents"] or not results["documents"][0]:
                return None

            # ChromaDB cosine空间返回distance, 越小越相似
            # cosine distance = 1 - cosine_similarity
            distance = results["distances"][0][0]
            similarity = 1.0 - distance

            if similarity < threshold:
                logger.debug(
                    "语义缓存未命中: similarity=%.4f < threshold=%.2f, query=%s",
                    similarity,
                    threshold,
                    query[:50],
                )
                return None

            cached_query = results["metadatas"][0][0].get("query", "")
            logger.info(
                "语义缓存命中: similarity=%.4f, cached_query=%s, query=%s",
                similarity,
                cached_query[:50],
                query[:50],
            )
            return results["documents"][0][0]

        except Exception as e:
            logger.warning("语义缓存查询异常, 降级为miss: %s", e)
            return None

    async def put(self, query: str, value: str) -> None:
        """存入缓存.

        Args:
            query: 查询文本(用于生成embedding)
            value: 缓存内容(JSON字符串)
        """
        try:
            await self._ensure_initialized()

            # 生成embedding
            embedding = await self._embed_query(query)

            # 唯一ID
            doc_id = f"rc_{int(time.time())}_{uuid.uuid4().hex[:8]}"

            # 存入ChromaDB
            self._collection.upsert(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[value],
                metadatas=[{"query": query, "cached_at": time.time()}],
            )

            logger.info("语义缓存已写入: query=%s, id=%s", query[:50], doc_id)

        except Exception as e:
            logger.warning("语义缓存写入异常: %s", e)

    async def cleanup(self, ttl: float | None = None) -> int:
        """清理过期条目.

        Args:
            ttl: 过期阈值(秒), 默认使用实例配置

        Returns:
            清理的条目数
        """
        ttl = ttl or self._ttl

        try:
            if not self._initialized:
                return 0

            cutoff = time.time() - ttl

            # 查询所有过期条目
            expired = self._collection.get(
                where={"cached_at": {"$lt": cutoff}},
                include=["metadatas"],
            )

            if not expired["ids"]:
                return 0

            self._collection.delete(ids=expired["ids"])
            count = len(expired["ids"])
            logger.info("语义缓存清理: 删除%d条过期条目", count)
            return count

        except Exception as e:
            logger.warning("语义缓存清理异常: %s", e)
            return 0

    def stats(self) -> dict[str, Any]:
        """获取缓存统计信息."""
        if not self._initialized:
            return {"status": "not_initialized", "count": 0}

        try:
            count = self._collection.count()
            return {
                "status": "active",
                "collection": self._collection_name,
                "cache_dir": self._cache_dir,
                "count": count,
                "ttl": self._ttl,
                "threshold": self._threshold,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}


# 全局单例: 按collection_name隔离(web_research用research_cache, professional_database用datapro_cache)
_semantic_caches: dict[str, SemanticCache] = {}


def get_semantic_cache(collection_name: str = _DEFAULT_COLLECTION) -> SemanticCache:
    """获取语义缓存实例(按collection隔离).

    Args:
        collection_name: ChromaDB collection名, 不同专家工具用独立collection避免语义空间污染

    Returns:
        对应collection的SemanticCache实例

    """
    global _semantic_caches
    if collection_name not in _semantic_caches:
        _semantic_caches[collection_name] = SemanticCache(
            collection_name=collection_name,
        )
    return _semantic_caches[collection_name]
