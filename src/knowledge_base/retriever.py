"""知识库检索器 - dense 召回 + rerank 精排 + top-K + 出处标注 + 字符预算硬上限.

上下文预算控制: 召回结果经 reranker 重排序后, 自高分向低分累加, 超出 budget_chars 即截断
(丢弃低分块), 保证返回主对话的内容既不撑爆上下文又优先保留最相关片段.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langchain_core.documents import Document

from src.knowledge_base.store import KnowledgeBaseStore

if TYPE_CHECKING:
    from src.inference.rerank.client import RerankClient

logger = logging.getLogger(__name__)

_DEFAULT_TOP_K = 8
_DEFAULT_BUDGET_CHARS = 4000


@dataclass
class RetrievalResult:
    """检索结果: 拼好的文本 + 命中块(带元数据)."""

    text: str
    chunks: list[Document] = field(default_factory=list)


class KnowledgeBaseRetriever:
    """从知识库向量库检索并格式化为带出处的文本."""

    def __init__(
        self,
        store: KnowledgeBaseStore,
        *,
        top_k: int = _DEFAULT_TOP_K,
        budget_chars: int = _DEFAULT_BUDGET_CHARS,
        reranker: RerankClient | None = None,
        rerank_instruction: str | None = None,
    ) -> None:
        self.store = store
        self.top_k = top_k
        self.budget_chars = budget_chars
        self._reranker = reranker
        self._rerank_instruction = rerank_instruction

    async def retrieve(
        self,
        query: str,
        *,
        doc_type: str | None = None,
        top_k: int | None = None,
    ) -> RetrievalResult:
        """检索并格式化.

        Args:
            query: 查询文本
            doc_type: 可选, 仅检索指定类型(book/paper/review/article)
            top_k: 覆盖默认召回数

        Returns:
            RetrievalResult; 无命中时 text 为提示信息

        """
        where = {"doc_type": doc_type} if doc_type else None
        docs = await self.store.search(query, top_k=top_k or self.top_k, where=where)
        if not docs:
            return RetrievalResult(text=f"[知识库未命中与'{query}'相关的内容]")

        docs = await self._rerank_docs(query, docs)

        formatted: list[str] = []
        kept: list[Document] = []
        used = 0
        for doc in docs:
            block = self._format(doc)
            if used + len(block) > self.budget_chars and kept:
                break
            formatted.append(block)
            kept.append(doc)
            used += len(block)

        return RetrievalResult(text="\n\n".join(formatted), chunks=kept)

    async def _rerank_docs(
        self,
        query: str,
        docs: list[Document],
    ) -> list[Document]:
        """对召回文档重排序, 失败时保持原始顺序."""
        if not self._reranker or len(docs) <= 1:
            return docs

        texts = [doc.page_content for doc in docs]
        results = await self._reranker.rerank(
            query,
            texts,
            instruction=self._rerank_instruction,
        )
        if not results:
            return docs

        reordered = []
        for r in results:
            if 0 <= r.index < len(docs):
                doc = docs[r.index]
                doc.metadata["rerank_score"] = r.relevance_score
                reordered.append(doc)
        return reordered or docs

    @staticmethod
    def _format(doc: Document) -> str:
        """格式化单块: 出处行(标题/类型/作者/章节) + 正文."""
        meta = doc.metadata
        parts = [f"标题: {meta.get('doc_title', '未知')}"]
        if meta.get("doc_type"):
            parts.append(f"类型: {meta['doc_type']}")
        if meta.get("author"):
            parts.append(f"作者: {meta['author']}")
        if meta.get("heading_chain"):
            parts.append(f"章节: {meta['heading_chain']}")
        header = "出处 | " + " | ".join(parts)
        return f"{header}\n{doc.page_content}"


__all__ = ["KnowledgeBaseRetriever", "RetrievalResult"]
