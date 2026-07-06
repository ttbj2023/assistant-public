"""学术搜索工具单元测试."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.external.academic_search_tool import (
    AcademicSearchTool,
    _extract_result_text,
    _format_papers,
)

_PAPERS_JSON = json.dumps(
    {
        "code": 0,
        "msg": "success",
        "trace_id": "abc123",
        "items": [
            {
                "name": "Attention Is All You Need",
                "url": "https://arxiv.org/pdf/1706.03762",
                "date_published": "2017-01-01T08:00:00.0000000",
                "snippet": "We propose a new architecture...",
                "abstract": "The dominant sequence transduction models...",
                "extra_data": {
                    "authors": "A Vaswani, N Shazeer, N Parmar",
                    "cite_by": 100000,
                    "doi": "https://doi.org/10.48550/arxiv.1706.03762",
                    "journal_title": "arXiv (Cornell University)",
                },
            },
            {
                "name": "Paper without abstract",
                "url": "https://example.com/paper2",
                "date_published": "2020-06-15T00:00:00",
                "snippet": "This is a snippet used as fallback.",
                "abstract": "",
                "extra_data": {"authors": "J Doe", "cite_by": 50},
            },
        ],
        "hint": "",
    },
    ensure_ascii=False,
)


# =============================================================================
# _extract_result_text 测试
# =============================================================================


class TestExtractResultText:
    """从fastmcp结果提取文本."""

    def test_string_passthrough(self):
        assert _extract_result_text("hello") == "hello"

    def test_content_with_text_attribute(self):
        result = MagicMock(content=[MagicMock(text="hello world")])
        assert _extract_result_text(result) == "hello world"

    def test_content_with_dict_text(self):
        result = MagicMock(content=[{"type": "text", "text": "dict text"}])
        assert _extract_result_text(result) == "dict text"

    def test_empty_returns_empty(self):
        assert _extract_result_text(None) == ""


# =============================================================================
# _format_papers 测试
# =============================================================================


class TestFormatPapers:
    """论文JSON格式化."""

    def test_normal_papers(self):
        result = _format_papers(_PAPERS_JSON, "transformer")
        assert "Attention Is All You Need" in result
        assert "A Vaswani" in result
        assert "引用100000" in result
        assert "2017" in result
        assert "arXiv (Cornell University)" in result
        assert "https://arxiv.org/pdf/1706.03762" in result

    def test_empty_items(self):
        empty_json = json.dumps({"code": 0, "items": []})
        result = _format_papers(empty_json, "nonexistent")
        assert "未找到" in result

    def test_non_json_passthrough(self):
        result = _format_papers("not a json", "query")
        assert result == "not a json"

    def test_abstract_empty_uses_snippet(self):
        result = _format_papers(_PAPERS_JSON, "test")
        assert "This is a snippet used as fallback." in result

    def test_missing_extra_data(self):
        data = json.dumps({"items": [{"name": "Test", "url": "http://x"}]})
        result = _format_papers(data, "q")
        assert "Test" in result
        assert "作者" not in result

    def test_paper_count_in_header(self):
        result = _format_papers(_PAPERS_JSON, "query")
        assert "共2篇" in result


# =============================================================================
# is_available 测试
# =============================================================================


class TestIsAvailable:
    """环境变量可用性检查."""

    @pytest.mark.asyncio
    async def test_available_when_key_set(self):
        with patch(
            "src.tools.external.academic_search_tool.get_credential",
            return_value="fake-key",
        ):
            t = AcademicSearchTool()
            assert await t.is_available() is True

    @pytest.mark.asyncio
    async def test_unavailable_when_key_missing(self):
        with patch(
            "src.tools.external.academic_search_tool.get_credential",
            return_value="",
        ):
            t = AcademicSearchTool()
            assert await t.is_available() is False


# =============================================================================
# _arun 测试 (mock fastmcp.Client)
# =============================================================================


class TestArun:
    """_arun端到端(mock DataPro调用)."""

    @pytest.mark.asyncio
    @patch("src.tools.external.academic_search_tool.get_credential")
    @patch("fastmcp.client.transports.StreamableHttpTransport")
    @patch("fastmcp.client.Client")
    async def test_should_format_papers(
        self, mock_client_cls, _mock_transport, mock_get_credential
    ):
        mock_get_credential.return_value = "fake-key"
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_result = MagicMock(content=[MagicMock(text=_PAPERS_JSON)])
        mock_client.call_tool.return_value = mock_result
        mock_client_cls.return_value = mock_client

        t = AcademicSearchTool()
        result = await t._arun("transformer attention")

        mock_client.call_tool.assert_awaited_once()
        assert "Attention Is All You Need" in result
        assert "引用100000" in result

    @pytest.mark.asyncio
    async def test_should_return_error_when_key_missing(self):
        with patch(
            "src.tools.external.academic_search_tool.get_credential",
            return_value="",
        ):
            t = AcademicSearchTool()
            result = await t._arun("query")
            assert "配置缺失" in result

    @pytest.mark.asyncio
    @patch("src.tools.external.academic_search_tool.get_credential")
    @patch("fastmcp.client.transports.StreamableHttpTransport")
    @patch("fastmcp.client.Client")
    async def test_should_handle_timeout(
        self, mock_client_cls, _mock_transport, mock_get_credential
    ):
        mock_get_credential.return_value = "fake-key"
        mock_client = AsyncMock()
        mock_client.__aenter__.side_effect = TimeoutError()
        mock_client_cls.return_value = mock_client

        t = AcademicSearchTool()
        result = await t._arun("query")
        assert "超时" in result
