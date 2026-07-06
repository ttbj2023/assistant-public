"""检索服务层 - 统一的对话检索接口.

提供符合Service架构的检索服务抽象,集成FilterParser和SmartDeduplication等功能,
实现标准化的全异步检索接口.

主要功能:
- 统一的检索服务接口
- 集成过滤器解析功能
- 全异步双路检索(SQL + 向量)
- 标准化的健康检查和错误处理
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, override

from langchain_core.documents import Document

if TYPE_CHECKING:
    from .conversation_service import ConversationService
    from .vector_service import VectorService

logger = logging.getLogger(__name__)


_TOKEN_SPLIT_RE = re.compile(r"[\s,，;；、|/\\()（）\[\]【】{}]+")  # noqa: RUF001
_MIN_TOKEN_LEN = 2


def _tokenize_query(query: str) -> list[str]:
    """将查询字符串切分为关键词 token.

    按空格及常见标点(中英文)切分, 过滤过短 token(长度 < _MIN_TOKEN_LEN).
    模型调用 search_memories 时已习惯按空格分隔中英文关键词(见实测日志),
    因此朴素切分即可, 不引入中文分词依赖.
    """
    if not query:
        return []
    return [
        token
        for token in _TOKEN_SPLIT_RE.split(query.strip())
        if len(token) >= _MIN_TOKEN_LEN
    ]


class RetrievalService(ABC):
    """检索服务抽象基类.

    定义统一的检索接口,符合项目的Service层架构设计.
    """

    @abstractmethod
    async def search_conversations(
        self,
        query: str,
        max_results: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[Document]:
        """搜索对话内容.

        Args:
            query: 查询字符串
            max_results: 最大结果数量,默认10,最大50
            filters: 过滤器字典,包含时间,轮次,关键词等过滤器

        Returns:
            相关Document列表

        Raises:
            ValueError: 参数验证失败
            RuntimeError: 检索执行失败

        """

    @abstractmethod
    async def search_with_filters(
        self,
        query: str,
        time_filter: str = "",
        max_results: int = 10,
    ) -> list[Document]:
        """使用格式化过滤器搜索对话.

        Args:
            query: 查询字符串
            time_filter: 时间过滤器,如 'yesterday', 'last_7_days', '2024-01-15'
            max_results: 最大结果数量,默认10,最大50

        Returns:
            相关Document列表

        """

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """检索服务健康检查.

        Returns:
            包含服务状态的字典

        """


class DualStageRetrievalService(RetrievalService):
    """全异步双阶段检索服务实现.

    直接使用异步SQL搜索和异步向量搜索,消除async->sync->async嵌套问题.
    集成FilterParser过滤器解析和SmartDeduplication智能去重.
    """

    def __init__(
        self,
        conversation_service: ConversationService,
        vector_service: VectorService | None,
        user_id: str,
        thread_id: str,
        enable_sql_search: bool = True,
        enable_vector_search: bool = True,
        max_results: int = 3,
    ) -> None:
        self.conversation_service = conversation_service
        self.vector_service = vector_service
        self.user_id = user_id
        self.thread_id = thread_id
        self.enable_sql_search = enable_sql_search
        self.enable_vector_search = enable_vector_search
        self.max_results = max_results
        self.enable_rerank = False

        self._initialized = False

        if not self.user_id or not self.thread_id:
            logger.warning("⚠️ user_id或thread_id为空,检索服务可能无法正常工作")

    async def _ensure_initialized(self) -> None:
        """确保检索服务已初始化 - 预初始化向量存储."""
        if self._initialized:
            return

        try:
            if self.vector_service and self.enable_vector_search:
                vector_store = getattr(self.vector_service, "_vector_store", None)
                if vector_store is not None:
                    await vector_store._ensure_initialized()
                    logger.info(
                        "✅ 向量存储预初始化完成: %s/%s",
                        self.user_id,
                        self.thread_id,
                    )

            self._initialized = True

        except Exception as e:
            logger.warning("⚠️ 向量存储预初始化失败,将降级到纯SQL检索: %s", e)
            self.enable_vector_search = False
            self._initialized = True

    @override
    async def search_conversations(
        self,
        query: str,
        max_results: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[Document]:
        """全异步搜索对话内容.

        直接异步调用SQL搜索和向量搜索,通过智能去重合并结果.

        Args:
            query: 查询字符串
            max_results: 最大结果数量
            filters: 过滤器字典

        Returns:
            相关Document列表

        """
        if not query or not query.strip():
            raise ValueError("查询字符串不能为空")

        if max_results <= 0:
            raise ValueError("max_results必须大于0")

        if max_results > 50:
            raise ValueError("max_results不能超过50")

        await self._ensure_initialized()

        try:
            sql_limit = max_results * 10
            vector_limit = max_results * 10

            sql_rounds: list[int] = []
            vector_rounds_with_scores: list[tuple[int, float]] = []

            if self.enable_sql_search:
                sql_rounds = await self._async_sql_search_rounds(
                    query, filters, sql_limit
                )

            if self.enable_vector_search and self.vector_service:
                vector_rounds_with_scores = await self._async_vector_search_rounds(
                    query,
                    filters,
                    vector_limit,
                )

            # 保留向量相似度分, 透传到最终 Document 作为真实 relevance 分
            # (向量命中用向量分, SQL-only 命中用中性分 0.5)
            round_score_map = dict(vector_rounds_with_scores)

            if not sql_rounds and not vector_rounds_with_scores:
                logger.info("SQL和向量检索均未返回结果")
                return await self._fallback_sql_search(query, max_results)

            from ..retrieval.smart_deduplication import smart_deduplication_with_scores

            candidate_rounds = smart_deduplication_with_scores(
                sql_rounds,
                vector_rounds_with_scores,
                max_candidates=sql_limit,
            )

            if not candidate_rounds:
                return await self._fallback_sql_search(query, max_results)

            documents = await self._async_get_final_documents(
                candidate_rounds,
                max_results,
                round_scores=round_score_map,
            )

            if not documents:
                return await self._fallback_sql_search(query, max_results)

            return documents

        except Exception as e:
            logger.error("❌ 对话检索失败: %s", e)
            logger.warning("⚠️ 降级到SQL搜索")
            return await self._fallback_sql_search(query, max_results)

    async def _async_sql_search_rounds(
        self,
        query: str,
        filters: dict[str, Any] | None,
        limit: int,
    ) -> list[int]:
        """异步SQL关键词检索,仅返回轮次号.

        Agent物理隔离: 数据库文件已按agent隔离,无需额外过滤.
        近期上下文召回职责由 Push(MemoryAssembler) 承担,
        本路专职字面关键词过滤(LIKE ANY 于 user_message/assistant_response),
        与向量语义检索互补: 精确锚定专有名词/实体名等向量可能欠加权的词.
        """
        try:
            terms = _tokenize_query(query)
            if not terms:
                return []

            round_range = filters.get("round_range") if filters else None

            return await self.conversation_service.search_rounds_by_keywords(
                self.user_id,
                self.thread_id,
                terms,
                round_range=round_range,
                limit=limit,
            )

        except Exception as e:
            logger.warning("⚠️ 异步SQL检索失败: %s", e)
            return []

    async def _async_vector_search_rounds(
        self,
        query: str,
        filters: dict[str, Any] | None,
        limit: int,
    ) -> list[tuple[int, float]]:
        """异步向量语义检索,返回(轮次号, 得分)列表."""
        try:
            vector_store = getattr(self.vector_service, "_vector_store", None)
            if vector_store is None:
                return []

            round_range = None
            if filters and "round_range" in filters:
                round_range = filters["round_range"]

            return await vector_store.search_rounds_only(
                query=query,
                max_results=limit,
                round_range=round_range,
            )

        except Exception as e:
            logger.warning("⚠️ 异步向量检索失败: %s", e)
            return []

    async def _async_get_final_documents(
        self,
        candidate_rounds: list[int],
        max_results: int,
        round_scores: dict[int, float] | None = None,
    ) -> list[Document]:
        """异步批量获取完整对话内容并转换为Document.

        Agent物理隔离: 数据库文件已按agent隔离,无需额外过滤.
        round_scores 透传真实相关度分(向量命中用向量分, SQL-only 命中回退中性分).
        """
        try:
            if not candidate_rounds:
                return []

            scores = round_scores or {}
            raw_conversations = (
                await self.conversation_service.get_conversations_by_rounds(
                    self.user_id,
                    self.thread_id,
                    candidate_rounds,
                )
            )

            if not raw_conversations:
                return []

            # 保持 candidate_rounds 的原始顺序 (去重)
            conv_by_round = {c.round_number: c for c in raw_conversations}
            conversations = []
            seen: set[int] = set()
            for r in candidate_rounds:
                if r in conv_by_round and r not in seen:
                    conversations.append(conv_by_round[r])
                    seen.add(r)

            documents = []
            for conv in conversations:
                content_parts = []
                user_msg = getattr(conv, "user_message", "")
                assistant_msg = getattr(conv, "assistant_response", "")
                if user_msg:
                    content_parts.append(f"用户: {user_msg}")
                if assistant_msg:
                    content_parts.append(f"助手: {assistant_msg}")

                content = "\n\n".join(content_parts)

                timestamp_value = (
                    conv.created_at.isoformat()
                    if hasattr(conv, "created_at") and conv.created_at
                    else None
                )
                metadata = {
                    "round_number": getattr(conv, "round_number", 0),
                    "user_id": self.user_id,
                    "thread_id": self.thread_id,
                    "created_at": timestamp_value,
                    "timestamp": timestamp_value,
                    "summary": getattr(conv, "summary", None),
                    "relevance_score": scores.get(
                        getattr(conv, "round_number", 0), 0.5
                    ),
                    "retrieval_type": "dual_stage_async",
                }

                documents.append(Document(page_content=content, metadata=metadata))

            return documents[:max_results]

        except Exception as e:
            logger.error("❌ 异步获取最终文档失败: %s", e)
            return []

    @override
    async def search_with_filters(
        self,
        query: str,
        time_filter: str = "",
        max_results: int = 10,
    ) -> list[Document]:
        """使用格式化过滤器搜索对话.

        Args:
            query: 查询字符串
            time_filter: 时间过滤器
            max_results: 最大结果数量

        Returns:
            相关Document列表

        """
        from ..retrieval.filter_parser import FilterParser

        filters = FilterParser.parse_filters(time_filter=time_filter)

        return await self.search_conversations(query, max_results, filters)

    @override
    async def health_check(self) -> dict[str, Any]:
        """检索服务健康检查."""
        try:
            await self._ensure_initialized()

            health_status = {
                "service_type": "dual_stage_retrieval",
                "user_id": self.user_id,
                "thread_id": self.thread_id,
                "initialized": self._initialized,
                "components": {
                    "conversation_service": self.conversation_service is not None,
                    "vector_service": self.vector_service is not None,
                },
                "features": {
                    "sql_search_enabled": self.enable_sql_search,
                    "vector_search_enabled": self.enable_vector_search,
                    "rerank_enabled": self.enable_rerank,
                },
                "retrieval_type": "dual_stage_async"
                if self.enable_vector_search
                else "sql_fallback",
                "status": "healthy",
            }

            if self.vector_service:
                try:
                    vector_health = await self.vector_service.health_check()
                    health_status["vector_health"] = vector_health
                    if vector_health.get("status") != "healthy":
                        health_status["status"] = "degraded"
                except Exception as e:
                    logger.debug("向量服务健康检查失败: %s", e)
                    health_status["status"] = "degraded"
                    health_status["vector_health_error"] = str(e)

            return health_status

        except Exception as e:
            logger.error("❌ 检索服务健康检查失败: %s", e)
            return {
                "service_type": "dual_stage_retrieval",
                "user_id": self.user_id,
                "thread_id": self.thread_id,
                "status": "unhealthy",
                "error": str(e),
                "initialized": self._initialized,
            }

    async def _fallback_sql_search(
        self,
        query: str,
        max_results: int,
    ) -> list[Document]:
        """SQL搜索降级方案 (Agent物理隔离,无需agent_id过滤)."""
        try:
            recent_items = await self.conversation_service.list_conversations(
                self.user_id,
                self.thread_id,
                limit=max_results,
            )

            if not recent_items:
                return []

            results = []
            query_lower = query.lower()

            for conv in recent_items:
                user_msg = getattr(conv, "user_message", "").lower()
                assistant_msg = getattr(conv, "assistant_response", "").lower()

                if query_lower in user_msg or query_lower in assistant_msg:
                    content_parts = []
                    raw_user = getattr(conv, "user_message", "")
                    raw_assistant = getattr(conv, "assistant_response", "")
                    if raw_user:
                        content_parts.append(f"用户: {raw_user}")
                    if raw_assistant:
                        content_parts.append(f"助手: {raw_assistant}")

                    content = "\n\n".join(content_parts)

                    metadata = {
                        "round_number": getattr(conv, "round_number", 0),
                        "user_id": self.user_id,
                        "thread_id": self.thread_id,
                        "created_at": getattr(conv, "created_at", None),
                        "timestamp": getattr(conv, "created_at", None),
                        "retrieval_type": "sql_fallback",
                    }

                    results.append(Document(page_content=content, metadata=metadata))

            return results[:max_results]

        except Exception as e:
            logger.error("❌ SQL降级搜索失败: %s", e)
            return []


__all__ = [
    "DualStageRetrievalService",
    "RetrievalService",
]
