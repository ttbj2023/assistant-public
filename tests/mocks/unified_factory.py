"""简化Mock工厂.

提供集成测试所需的LLM/Embedding/VectorStore Mock对象。
设计原则: 简单直接, 每种类型只保留一个创建方法, 按需使用。
"""

import hashlib
import math
from unittest.mock import Mock

from langchain_core.documents import Document
from langchain_core.messages import AIMessage


class UnifiedMockFactory:
    """简化Mock工厂, 提供LLM/Embedding/VectorStore Mock.

    使用示例:
        llm = UnifiedMockFactory.create_llm()
        embeddings = UnifiedMockFactory.create_embeddings(realistic=True)
        vector_store = UnifiedMockFactory.create_advanced_vector_store()
    """

    DEFAULT_EMBEDDING_DIMENSIONS = 384
    DEFAULT_LLM_RESPONSE = "这是一个模拟的LLM响应"

    @classmethod
    def create_llm(cls, response_content: str | None = None) -> Mock:
        """创建LLM Mock对象.

        Args:
            response_content: 默认响应内容

        Returns:
            配置好的LLM Mock对象, 支持invoke/ainvoke/astream/abatch
        """
        mock_llm = Mock()
        content = response_content or cls.DEFAULT_LLM_RESPONSE

        mock_llm.invoke.return_value = AIMessage(content=content)

        async def _async_handler(*args, **kwargs):
            return AIMessage(content=content)

        mock_llm.ainvoke = _async_handler
        mock_llm.astream = _async_handler
        mock_llm.abatch = _async_handler

        return mock_llm

    @classmethod
    def create_embeddings(
        cls,
        dimensions: int | None = None,
        realistic: bool = False,
    ) -> Mock:
        """创建嵌入模型Mock对象.

        Args:
            dimensions: 嵌入向量维度, 默认384
            realistic: True时基于文本内容生成可预测向量, False时返回固定向量

        Returns:
            配置好的嵌入模型Mock对象
        """
        if realistic:
            return cls._create_realistic_embeddings(dimensions)
        return cls._create_simple_embeddings(dimensions)

    @classmethod
    def _create_simple_embeddings(cls, dimensions: int | None = None) -> Mock:
        """创建简单嵌入模型Mock (固定向量)."""
        mock_embeddings = Mock()
        dim = dimensions or cls.DEFAULT_EMBEDDING_DIMENSIONS

        def embed_query(text: str) -> list[float]:
            return [0.1] * dim

        def embed_documents(texts: list[str]) -> list[list[float]]:
            return [[0.1] * dim for _ in texts]

        async def aembed_query(text: str) -> list[float]:
            return [0.1] * dim

        async def aembed_documents(texts: list[str]) -> list[list[float]]:
            return [[0.1] * dim for _ in texts]

        mock_embeddings.embed_query = embed_query
        mock_embeddings.embed_documents = embed_documents
        mock_embeddings.aembed_query = aembed_query
        mock_embeddings.aembed_documents = aembed_documents
        mock_embeddings.dimensions = dim
        mock_embeddings.call_count = 0
        mock_embeddings.call_history = []

        return mock_embeddings

    @classmethod
    def _create_realistic_embeddings(cls, dimensions: int | None = None) -> Mock:
        """创建真实感嵌入模型Mock (基于文本内容生成可预测向量)."""
        mock_embeddings = Mock()
        dim = dimensions or cls.DEFAULT_EMBEDDING_DIMENSIONS

        def embed_query(text: str) -> list[float]:
            mock_embeddings.call_count += 1
            mock_embeddings.call_history.append(("embed_query", text))
            return cls._generate_text_based_vector(text, dim)

        def embed_documents(texts: list[str]) -> list[list[float]]:
            mock_embeddings.call_count += 1
            mock_embeddings.call_history.append(("embed_documents", texts))
            return [cls._generate_text_based_vector(text, dim) for text in texts]

        async def aembed_query(text: str) -> list[float]:
            mock_embeddings.call_count += 1
            mock_embeddings.call_history.append(("aembed_query", text))
            return cls._generate_text_based_vector(text, dim)

        async def aembed_documents(texts: list[str]) -> list[list[float]]:
            mock_embeddings.call_count += 1
            mock_embeddings.call_history.append(("aembed_documents", texts))
            return [cls._generate_text_based_vector(text, dim) for text in texts]

        mock_embeddings.embed_query = embed_query
        mock_embeddings.embed_documents = embed_documents
        mock_embeddings.aembed_query = aembed_query
        mock_embeddings.aembed_documents = aembed_documents
        mock_embeddings.dimensions = dim
        mock_embeddings.call_count = 0
        mock_embeddings.call_history = []
        mock_embeddings.reset = lambda: (
            setattr(mock_embeddings, "call_count", 0)
            or mock_embeddings.call_history.clear()
        )

        return mock_embeddings

    @classmethod
    def create_advanced_vector_store(
        cls, dimensions: int | None = None, collection_name: str = "test_collection"
    ) -> Mock:
        """创建高级向量存储Mock对象, 支持真实的相似性搜索.

        Args:
            dimensions: 向量维度, 默认384
            collection_name: 集合名称

        Returns:
            配置好的向量存储Mock对象
        """
        mock_store = Mock()
        dim = dimensions or cls.DEFAULT_EMBEDDING_DIMENSIONS

        mock_store.documents = {}
        mock_store.dimensions = dim
        mock_store.collection_name = collection_name
        mock_store.call_count = 0
        mock_store.call_history = []

        embedding_mock = cls._create_realistic_embeddings(dim)

        async def add_documents(
            documents: list[Document], ids: list[str] | None = None
        ):
            mock_store.call_count += 1
            mock_store.call_history.append(("add_documents", documents, ids))

            if ids is None:
                ids = [f"doc_{i}" for i in range(len(documents))]

            for doc, doc_id in zip(documents, ids):
                vector = embedding_mock.embed_query(doc.page_content)
                mock_store.documents[doc_id] = {
                    "vector": vector,
                    "content": doc.page_content,
                    "metadata": doc.metadata or {},
                }

            mock_store.document_count = len(mock_store.documents)
            return ids

        async def similarity_search(
            query: str, k: int = 4, filter_dict: dict | None = None
        ) -> list[Document]:
            mock_store.call_count += 1
            mock_store.call_history.append(("similarity_search", query, k, filter_dict))

            query_vector = embedding_mock.embed_query(query)

            scored_docs = []
            for doc_id, doc_data in mock_store.documents.items():
                if filter_dict:
                    metadata = doc_data["metadata"]
                    if not all(metadata.get(k) == v for k, v in filter_dict.items()):
                        continue

                similarity = cls._cosine_similarity(query_vector, doc_data["vector"])
                scored_docs.append((doc_id, doc_data, similarity))

            scored_docs.sort(key=lambda x: x[2], reverse=True)

            results = []
            for doc_id, doc_data, similarity in scored_docs[:k]:
                metadata = doc_data["metadata"].copy()
                metadata["similarity_score"] = similarity
                metadata["document_id"] = doc_id
                results.append(
                    Document(page_content=doc_data["content"], metadata=metadata)
                )

            return results

        async def search_rounds_only(
            query_embedding: list[float], limit: int = 5
        ) -> list[int]:
            mock_store.call_count += 1
            mock_store.call_history.append((
                "search_rounds_only",
                query_embedding,
                limit,
            ))
            available_rounds = list(
                range(1, min(limit + 1, len(mock_store.documents) + 1))
            )
            return available_rounds

        mock_store.add_documents = add_documents
        mock_store.similarity_search = similarity_search
        mock_store.search_rounds_only = search_rounds_only
        mock_store.document_count = 0
        mock_store.collection_count = 1

        def update_document_count():
            mock_store.document_count = len(mock_store.documents)
            return mock_store.document_count

        mock_store.update_document_count = update_document_count

        def reset():
            mock_store.documents.clear()
            mock_store.call_count = 0
            mock_store.call_history.clear()
            mock_store.document_count = 0

        mock_store.reset = reset

        return mock_store

    @classmethod
    def _generate_text_based_vector(cls, text: str, dimensions: int) -> list[float]:
        """基于文本哈希生成可预测的向量.

        相同文本总是产生相同向量, 不同文本产生不同向量.
        """
        hash_obj = hashlib.md5(text.encode("utf-8"))
        hash_hex = hash_obj.hexdigest()

        vector = []
        for i in range(0, len(hash_hex), 2):
            byte_val = int(hash_hex[i : i + 2], 16)
            normalized_val = byte_val / 255.0
            vector.append(normalized_val)

        while len(vector) < dimensions:
            vector.extend(vector)
        return vector[:dimensions]

    @classmethod
    def _cosine_similarity(cls, vec1: list[float], vec2: list[float]) -> float:
        """计算余弦相似度."""
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)
