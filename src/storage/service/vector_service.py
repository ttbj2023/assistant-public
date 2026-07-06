"""向量存储服务.

负责向量存储相关的业务逻辑:
- 向量存储的创建和管理
- 文档添加和检索
- 向量搜索的业务逻辑封装
- 与其他存储服务的协调
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain_core.documents import Document

from src.storage.models.conversation import ConversationData

if TYPE_CHECKING:
    from src.storage.langchain_vector_store import LangChainVectorStore

logger = logging.getLogger(__name__)


class VectorService:
    """向量存储业务服务.

    提供向量存储的高级业务接口,封装了底层 LangChainVectorStore 的复杂性.
    在构造时立即初始化向量存储,确保API一致性.
    """

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        vector_store: LangChainVectorStore,
    ) -> None:
        """初始化向量存储服务.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            vector_store: 已初始化的向量存储实例

        """
        self.user_id = user_id
        self.thread_id = thread_id
        self._vector_store: LangChainVectorStore | None = vector_store
        self._initialized = True

        logger.debug(
            "🔗 初始化VectorService: user_id=%s, thread_id=%s",
            user_id,
            thread_id,
        )

    @property
    def vector_store(self) -> LangChainVectorStore | None:
        """获取向量存储实例."""
        return self._vector_store

    async def add_conversation_content(
        self,
        conversation_data: ConversationData,
    ) -> str:
        """添加对话内容到向量存储.

        Args:
            conversation_data: 对话数据对象

        Returns:
            添加的文档ID

        """
        try:
            doc_id = await self._vector_store.add_conversation_round(
                round_number=conversation_data.round_number,
                user_message=conversation_data.user_message,
                assistant_response=conversation_data.assistant_response,
                agent_id=conversation_data.agent_id,
            )

            logger.debug(
                "💬 向量存储添加对话轮次 %s (agent=%s)",
                conversation_data.round_number,
                conversation_data.agent_id,
            )
            return doc_id

        except Exception as e:
            logger.error("❌ 添加对话内容到向量存储失败: %s", e)
            raise RuntimeError(f"向量存储操作失败: {e}") from e

    async def search_conversations(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[Document]:
        """搜索对话内容.

        Args:
            query: 搜索查询字符串
            max_results: 最大返回结果数

        Returns:
            匹配的文档列表

        """
        try:
            if not query or not query.strip():
                logger.warning("⚠️ 搜索查询为空,返回空结果")
                return []

            results = await self._vector_store.similarity_search(
                query=query.strip(),
                max_results=max_results,
            )

            logger.debug(f"🔍 向量搜索返回 {len(results)} 个结果")
            return results

        except Exception as e:
            logger.error("❌ 向量搜索失败: %s", e)
            raise RuntimeError(f"向量搜索操作失败: {e}") from e

    async def get_collection_stats(self) -> dict[str, Any]:
        """获取向量存储集合统计信息.

        Returns:
            集合统计信息字典

        """
        try:
            stats = self._vector_store.get_collection_stats()
            logger.debug(f"📊 向量集合统计: {stats.get('document_count', 0)} 个文档")
            return stats

        except Exception as e:
            logger.error("❌ 获取向量集合统计失败: %s", e)
            return {"error": str(e), "status": "failed"}

    async def health_check(self) -> dict[str, Any]:
        """健康检查.

        Returns:
            健康检查结果

        """
        try:
            stats = await self.get_collection_stats()

            return {
                "status": "healthy",
                "vector_store_initialized": True,
                "collection_stats": stats,
                "error": None,
            }

        except Exception as e:
            logger.error("❌ 向量服务健康检查失败: %s", e)
            return {
                "status": "unhealthy",
                "vector_store_initialized": False,
                "collection_stats": None,
                "error": str(e),
            }

    def close(self) -> None:
        """关闭向量存储服务,清理资源."""
        try:
            if self._vector_store:
                self._vector_store.close()
                self._vector_store = None

            self._initialized = False
            logger.debug(f"🔌 VectorService已关闭: {self.user_id}_{self.thread_id}")

        except Exception as e:
            logger.warning("⚠️ 关闭VectorService时出现警告: %s", e)

    def __del__(self) -> None:
        """析构函数,确保资源清理."""
        self.close()
