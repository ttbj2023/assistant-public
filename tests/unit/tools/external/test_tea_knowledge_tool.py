"""TeaKnowledgeTool 单元测试 - mock cache/store/retriever 验证编排."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.knowledge_base.retriever import RetrievalResult
from src.tools.external.tea_knowledge_tool import TeaKnowledgeTool

_MOD = "src.tools.external.tea_knowledge_tool"


@pytest.fixture
def tool() -> TeaKnowledgeTool:
    return TeaKnowledgeTool()


class TestToolIdentity:
    def test_tool_has_tea_focused_identity(self, tool):
        assert tool.name == "tea_knowledge"
        assert tool.kb_name == "tea"
        assert "茶" in tool.summary
        assert "茶" in tool.description
        assert any("茶" in kw for kw in tool.search_keywords)


class TestIsAvailable:
    async def test_true_when_store_has_data(self, tool):
        with patch(f"{_MOD}.KnowledgeBaseStore") as store_cls:
            store_cls.has_data.return_value = True
            assert await tool.is_available() is True
            store_cls.has_data.assert_called_once_with("tea")

    async def test_false_when_no_data(self, tool):
        with patch(f"{_MOD}.KnowledgeBaseStore") as store_cls:
            store_cls.has_data.return_value = False
            assert await tool.is_available() is False

    async def test_custom_persist_directory_checks_dir(self, tmp_path):
        tool = TeaKnowledgeTool(persist_directory=str(tmp_path))
        (tmp_path / "x").write_text("d", encoding="utf-8")
        assert await tool.is_available() is True

    async def test_custom_persist_directory_empty(self, tmp_path):
        tool = TeaKnowledgeTool(persist_directory=str(tmp_path / "空"))
        assert await tool.is_available() is False


class TestArun:
    async def test_cache_hit_returns_cached(self, tool):
        cache = MagicMock()
        cache.get = AsyncMock(return_value="缓存的结果")
        with (
            patch(f"{_MOD}.get_semantic_cache", return_value=cache),
            patch(f"{_MOD}.KnowledgeBaseRetriever") as retriever_cls,
        ):
            result = await tool._arun("龙井")
        assert result == "缓存的结果"
        retriever_cls.assert_not_called()

    async def test_cache_miss_calls_retriever_and_caches(self, tool):
        cache = MagicMock()
        cache.get = AsyncMock(return_value=None)
        cache.put = AsyncMock()

        retriever = MagicMock()
        retriever.retrieve = AsyncMock(
            return_value=RetrievalResult(text="检索结果", chunks=[MagicMock()])
        )

        with (
            patch(f"{_MOD}.get_semantic_cache", return_value=cache),
            patch(f"{_MOD}.KnowledgeBaseStore"),
            patch(f"{_MOD}.KnowledgeBaseRetriever", return_value=retriever),
        ):
            result = await tool._arun("龙井")

        assert result == "检索结果"
        cache.put.assert_awaited_once()

    async def test_doc_type_forwarded_to_retriever(self, tool):
        cache = MagicMock()
        cache.get = AsyncMock(return_value=None)
        cache.put = AsyncMock()
        retriever = MagicMock()
        retriever.retrieve = AsyncMock(
            return_value=RetrievalResult(text="x", chunks=[])
        )
        with (
            patch(f"{_MOD}.get_semantic_cache", return_value=cache),
            patch(f"{_MOD}.KnowledgeBaseStore"),
            patch(f"{_MOD}.KnowledgeBaseRetriever", return_value=retriever),
        ):
            await tool._arun("绿茶", doc_type="review")
        retriever.retrieve.assert_awaited_once_with("绿茶", doc_type="review")

    async def test_store_closed_after_retrieve(self, tool):
        cache = MagicMock()
        cache.get = AsyncMock(return_value=None)
        cache.put = AsyncMock()
        store = MagicMock()
        retriever = MagicMock()
        retriever.retrieve = AsyncMock(
            return_value=RetrievalResult(text="x", chunks=[])
        )
        with (
            patch(f"{_MOD}.get_semantic_cache", return_value=cache),
            patch(f"{_MOD}.KnowledgeBaseStore", return_value=store),
            patch(f"{_MOD}.KnowledgeBaseRetriever", return_value=retriever),
        ):
            await tool._arun("龙井")
        store.close.assert_called_once()
