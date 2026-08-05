"""KnowledgeBaseStore 单元测试 - 真实临时 Chroma + mock embeddings."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document

from src.knowledge_base.store import KnowledgeBaseStore
from tests.mocks.unified_factory import UnifiedMockFactory


@pytest.fixture
def store(tmp_path):
    return KnowledgeBaseStore(
        "tea",
        persist_directory=str(tmp_path / "kb"),
        embeddings=UnifiedMockFactory.create_embeddings(realistic=True),
    )


def _doc(content: str, **meta) -> Document:
    return Document(page_content=content, metadata=meta)


class TestAddAndCount:
    async def test_add_then_count(self, store):
        await store.add_documents(
            [_doc("茶树品种", doc_ref="a"), _doc("冲泡方法", doc_ref="b")],
            ids=["a_0", "b_0"],
        )
        assert store.count() == 2

    async def test_upsert_overwrites_same_id(self, store):
        await store.add_documents([_doc("旧", doc_ref="a")], ids=["a_0"])
        await store.add_documents([_doc("新", doc_ref="a")], ids=["a_0"])
        assert store.count() == 1

    async def test_large_add_embeds_in_batches(self, tmp_path):
        """大批量写入分批 embed: 整篇文档数百块一次请求会打爆 embedding 超时."""
        embeddings = MagicMock()
        embeddings.aembed_documents = AsyncMock(
            side_effect=lambda texts: [[0.1] * 8 for _ in texts]
        )
        store = KnowledgeBaseStore(
            "tea",
            persist_directory=str(tmp_path / "kb"),
            embeddings=embeddings,
        )
        docs = [_doc(f"内容{i}", doc_ref="a") for i in range(40)]
        await store.add_documents(docs, ids=[f"a_{i}" for i in range(40)])
        batch_sizes = [
            len(c.args[0]) for c in embeddings.aembed_documents.call_args_list
        ]
        assert batch_sizes == [16, 16, 8]
        assert store.count() == 40


class TestSearch:
    async def test_returns_most_relevant_first(self, store):
        await store.add_documents(
            [
                _doc("西湖龙井是绿茶", doc_ref="a"),
                _doc("コーヒーの入れ方", doc_ref="b"),
                _doc("龙井茶的冲泡水温", doc_ref="c"),
            ],
            ids=["a_0", "b_0", "c_0"],
        )
        results = await store.search("龙井", top_k=2)
        assert len(results) == 2
        assert all("score" in r.metadata for r in results)
        # 分数降序
        assert results[0].metadata["score"] >= results[1].metadata["score"]

    async def test_where_filter_by_doc_type(self, store):
        await store.add_documents(
            [
                _doc("论文摘要A", doc_ref="p1", doc_type="paper"),
                _doc("评测摘要B", doc_ref="r1", doc_type="review"),
            ],
            ids=["p1_0", "r1_0"],
        )
        results = await store.search("摘要", top_k=5, where={"doc_type": "paper"})
        assert len(results) == 1
        assert results[0].metadata["doc_type"] == "paper"

    async def test_empty_store_returns_empty(self, store):
        assert await store.search("任何查询") == []


class TestDeleteByDocRef:
    async def test_delete_removes_only_target_doc(self, store):
        await store.add_documents(
            [
                _doc("文档甲块1", doc_ref="甲"),
                _doc("文档甲块2", doc_ref="甲"),
                _doc("文档乙块1", doc_ref="乙"),
            ],
            ids=["甲_0", "甲_1", "乙_0"],
        )
        await store.delete_by_doc_ref("甲")
        assert store.count() == 1


class TestMetadataCleaning:
    async def test_non_scalar_metadata_serialized(self, store):
        await store.add_documents(
            [_doc("内容", doc_ref="a", tags=["x", "y"])], ids=["a_0"]
        )
        results = await store.search("内容", top_k=1)
        assert results[0].metadata["doc_ref"] == "a"
