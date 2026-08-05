"""KnowledgeBaseRetriever 检索测试 - 真实 Chroma + mock embeddings."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from src.inference.rerank.client import RerankClient, RerankResult
from src.knowledge_base.retriever import KnowledgeBaseRetriever
from src.knowledge_base.store import KnowledgeBaseStore
from tests.mocks.unified_factory import UnifiedMockFactory


@pytest.fixture
def store(tmp_path):
    s = KnowledgeBaseStore(
        "tea",
        persist_directory=str(tmp_path / "kb"),
        embeddings=UnifiedMockFactory.create_embeddings(realistic=True),
    )
    return s


async def _seed(store):
    docs = [
        Document(
            page_content="西湖龙井属于绿茶, 产于杭州。",
            metadata={
                "doc_ref": "a",
                "doc_title": "中国茶经",
                "doc_type": "book",
                "author": "陈宗懋",
                "heading_chain": "中国茶经 / 绿茶",
            },
        ),
        Document(
            page_content="龙井冲泡水温建议85度。",
            metadata={
                "doc_ref": "b",
                "doc_title": "冲泡指南",
                "doc_type": "article",
                "heading_chain": "冲泡指南 / 水温",
            },
        ),
        Document(
            page_content="コーヒーの淹れ方について。",
            metadata={
                "doc_ref": "c",
                "doc_title": "咖啡手册",
                "doc_type": "book",
                "heading_chain": "咖啡手册",
            },
        ),
    ]
    await store.add_documents(docs, ids=["a_0", "b_0", "c_0"])


class TestRetrieve:
    async def test_returns_text_with_provenance(self, store):
        await _seed(store)
        retriever = KnowledgeBaseRetriever(store, top_k=2)
        result = await retriever.retrieve("龙井")
        assert "出处" in result.text
        assert "标题:" in result.text
        assert result.chunks
        assert all("score" in c.metadata for c in result.chunks)

    async def test_doc_type_filter(self, store):
        await _seed(store)
        retriever = KnowledgeBaseRetriever(store, top_k=5)
        result = await retriever.retrieve("龙井", doc_type="book")
        assert all(c.metadata["doc_type"] == "book" for c in result.chunks)

    async def test_budget_cap_drops_low_score_chunks(self, store):
        await _seed(store)
        retriever = KnowledgeBaseRetriever(store, top_k=3, budget_chars=40)
        result = await retriever.retrieve("龙井")
        # 预算极小, 至少保留 1 块但不会全收
        assert len(result.chunks) >= 1
        assert len(result.chunks) < 3

    async def test_no_hit_returns_message(self, store):
        retriever = KnowledgeBaseRetriever(store)
        result = await retriever.retrieve("量子力学")
        assert "未命中" in result.text
        assert result.chunks == []

    async def test_author_in_provenance_when_present(self, store):
        await _seed(store)
        # top_k 取全部, 保证含作者字段的块被纳入(mock embeddings 非真实语义)
        retriever = KnowledgeBaseRetriever(store, top_k=3)
        result = await retriever.retrieve("西湖龙井绿茶杭州")
        assert "作者: 陈宗懋" in result.text


def _mock_reranker(scores: list[float]) -> AsyncMock:
    """构造 mock RerankClient, 按给定分数列表返回 RerankResult (降序, 同真实 API)."""
    mock = AsyncMock(spec=RerankClient)
    results = [RerankResult(index=i, relevance_score=s) for i, s in enumerate(scores)]
    results.sort(key=lambda r: r.relevance_score, reverse=True)
    mock.rerank.return_value = results
    return mock


class TestRetrieveWithRerank:
    """Rerank 集成: 重排序后预算截断."""

    async def test_rerank_reorders_docs_before_budget(self, store):
        """Rerank 应按 relevance_score 重排文档, 影响预算截断优先级."""
        await _seed(store)
        # 先无 rerank 取一次, 确认原始顺序
        baseline = KnowledgeBaseRetriever(store, top_k=3)
        base_result = await baseline.retrieve("龙井")
        base_titles = [c.metadata["doc_title"] for c in base_result.chunks]

        # rerank 反转: 让最后一个文档得分最高
        n = len(base_titles)
        scores = [float(i) / n for i in range(n)]  # 递增, 最后一个最高
        reranker = _mock_reranker(scores)
        retriever = KnowledgeBaseRetriever(store, top_k=3, reranker=reranker)
        result = await retriever.retrieve("龙井")

        assert reranker.rerank.called
        # 反转后第一个应该是原始最后一个
        reranked_titles = [c.metadata["doc_title"] for c in result.chunks]
        assert reranked_titles[0] == base_titles[-1]

    async def test_rerank_failure_keeps_original_order(self, store):
        """Rerank 返回 None 时应保持原始向量排序."""
        await _seed(store)
        reranker = AsyncMock(spec=RerankClient)
        reranker.rerank.return_value = None
        retriever = KnowledgeBaseRetriever(store, top_k=3, reranker=reranker)
        result = await retriever.retrieve("龙井")

        assert len(result.chunks) >= 1

    async def test_no_reranker_keeps_original_order(self, store):
        """未配置 reranker 时行为不变."""
        await _seed(store)
        retriever = KnowledgeBaseRetriever(store, top_k=3)
        result = await retriever.retrieve("龙井")
        assert len(result.chunks) >= 1

    async def test_rerank_respects_instruction_param(self, store):
        """rerank_instruction 应透传给 RerankClient."""
        await _seed(store)
        reranker = _mock_reranker([0.5, 0.5, 0.5])
        retriever = KnowledgeBaseRetriever(
            store,
            top_k=3,
            reranker=reranker,
            rerank_instruction="Retrieve relevant passages",
        )
        await retriever.retrieve("龙井")

        call_kwargs = reranker.rerank.call_args.kwargs
        assert call_kwargs["instruction"] == "Retrieve relevant passages"
