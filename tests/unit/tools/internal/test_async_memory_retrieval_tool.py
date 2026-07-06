"""AsyncMemoryRetrievalTool 单元测试.

测试记忆检索工具的业务逻辑: 参数验证, 服务调用, 结果格式化, 配置更新.
Mock外部依赖: create_retrieval_service, 检索服务实例.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.internal.async_memory_retrieval_tool import AsyncMemoryRetrievalTool


@pytest.fixture
def tool():
    return AsyncMemoryRetrievalTool(user_id="u1", thread_id="t1", agent_id="a1")


@pytest.fixture
def mock_service():
    svc = AsyncMock()
    svc.search_conversations = AsyncMock(return_value=[])
    svc.search_with_filters = AsyncMock(return_value=[])
    svc.health_check = AsyncMock(return_value={"overall": True, "status": "healthy"})
    return svc


def _inject_service(tool, mock_service):
    """通过mock _get_service注入服务实例, 让源码自行缓存到_retrieval_service."""

    async def _fake_get_service():
        object.__setattr__(tool, "_retrieval_service", mock_service)
        return mock_service

    return patch.object(tool, "_get_service", side_effect=_fake_get_service)


class TestInit:
    """测试初始化."""

    def test_empty_user_id_raises(self):
        with pytest.raises(ValueError, match="用户ID不能为空"):
            AsyncMemoryRetrievalTool(user_id="", thread_id="t1", agent_id="a1")

    def test_empty_thread_id_raises(self):
        with pytest.raises(ValueError, match="线程ID不能为空"):
            AsyncMemoryRetrievalTool(user_id="u1", thread_id="", agent_id="a1")

    def test_whitespace_user_id_raises(self):
        with pytest.raises(ValueError, match="用户ID不能为空"):
            AsyncMemoryRetrievalTool(user_id="  ", thread_id="t1", agent_id="a1")


class TestFormatDocumentsToResults:
    """测试文档格式化."""

    def test_formats_documents_with_metadata(self, tool):
        doc1 = MagicMock()
        doc1.page_content = "content1"
        doc1.metadata = {
            "timestamp": "2024-01-01",
            "round_number": 5,
            "relevance_score": 0.95,
        }

        doc2 = MagicMock()
        doc2.page_content = "content2"
        doc2.metadata = {"timestamp": "2024-01-02"}

        results = tool._format_documents_to_results([doc1, doc2])
        assert len(results) == 2
        assert results[0]["content"] == "content1"
        assert results[0]["relevance"] == 0.95
        assert results[0]["round_number"] == 5
        # doc2没有relevance_score, 源码使用 0.9 - i*0.1 = 0.8 作为默认值
        assert results[1]["relevance"] == pytest.approx(0.8)

    def test_empty_documents_returns_empty(self, tool):
        results = tool._format_documents_to_results([])
        assert results == []

    def test_long_content_truncated(self, tool):
        """超长文档内容应被截断到2000字符."""
        doc = MagicMock()
        doc.page_content = "x" * 5000
        doc.metadata = {"timestamp": "2024-01-01"}

        results = tool._format_documents_to_results([doc])

        assert len(results[0]["content"]) < 5000
        assert "已截断" in results[0]["content"]


class TestArun:
    """测试异步执行."""

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self, tool):
        result = await tool._arun(query="", max_results=10)
        parsed = json.loads(result)
        assert parsed["success"] is False

    @pytest.mark.asyncio
    async def test_zero_max_results_returns_error(self, tool):
        result = await tool._arun(query="test", max_results=0)
        parsed = json.loads(result)
        assert parsed["success"] is False

    @pytest.mark.asyncio
    async def test_max_results_over_50_returns_error(self, tool):
        result = await tool._arun(query="test", max_results=100)
        parsed = json.loads(result)
        assert parsed["success"] is False

    @pytest.mark.asyncio
    async def test_successful_search(self, tool, mock_service):
        doc = MagicMock()
        doc.page_content = "found content"
        doc.metadata = {"timestamp": "2024-01-01", "round_number": 3}
        mock_service.search_conversations = AsyncMock(return_value=[doc])

        with _inject_service(tool, mock_service):
            result = await tool._arun(query="test query", max_results=5)

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["total_count"] == 1
        assert parsed["results"][0]["content"] == "found content"

    @pytest.mark.asyncio
    async def test_fallback_on_primary_failure(self, tool, mock_service):
        """主路径失败时应降级到基础检索."""
        # 第一次调用 search_conversations (在 try 里) 抛异常
        # 第二次调用 search_conversations (在 except 里) 成功返回空列表
        mock_service.search_conversations = AsyncMock(
            side_effect=[Exception("primary failed"), []]
        )

        with _inject_service(tool, mock_service):
            result = await tool._arun(query="test", max_results=5)

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_service.search_conversations.call_count == 2

    @pytest.mark.asyncio
    async def test_service_unavailable_returns_error(self, tool):
        with patch.object(
            tool, "_get_service", side_effect=RuntimeError("init failed")
        ):
            result = await tool._arun(query="test", max_results=5)

        parsed = json.loads(result)
        assert parsed["success"] is False


class TestGetRelevantDocuments:
    """测试获取相关文档."""

    @pytest.mark.asyncio
    async def test_returns_documents(self, tool, mock_service):
        doc = MagicMock()
        doc.page_content = "doc1"
        doc.metadata = {}
        mock_service.search_conversations = AsyncMock(return_value=[doc])

        with _inject_service(tool, mock_service):
            docs = await tool.aget_relevant_documents("test")

        assert len(docs) == 1

    @pytest.mark.asyncio
    async def test_fallback_on_primary_failure(self, tool, mock_service):
        """主路径失败时合理降级: 返回空列表, 不调用会二次失败的 search_with_filters.

        降级方向与 _arun 一致 (缺陷 B 修复): search_conversations 已是最基础路径,
        其失败后无更基础降级, 返回空列表由调用方处理.
        """
        mock_service.search_conversations = AsyncMock(side_effect=Exception("fail"))
        mock_service.search_with_filters = AsyncMock(return_value=[])

        with _inject_service(tool, mock_service):
            docs = await tool.aget_relevant_documents("test")

        assert docs == []
        mock_service.search_with_filters.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_service_returns_empty(self, tool):
        with patch.object(tool, "_get_service", side_effect=Exception("no service")):
            docs = await tool.aget_relevant_documents("test")
        assert docs == []


class TestHealthCheck:
    """测试健康检查."""

    @pytest.mark.asyncio
    async def test_healthy_service(self, tool, mock_service):
        mock_service.health_check = AsyncMock(
            return_value={"overall": True, "status": "healthy"}
        )

        with _inject_service(tool, mock_service):
            result = await tool.ahealth_check()

        assert result["overall"] is True
        assert result["tool_user_id"] == "u1"

    @pytest.mark.asyncio
    async def test_no_service_returns_unhealthy(self, tool):
        with patch.object(tool, "_get_service", side_effect=Exception("fail")):
            result = await tool.ahealth_check()

        assert result["overall"] is False
        assert result["status"] == "unhealthy"
