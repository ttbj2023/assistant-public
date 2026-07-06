"""grounding fallback 模块单元测试.

验证 search_fallback / url_context_fallback / maps_fallback 在 Gemini
不可用时的等效降级行为. Mock 外部检索工具和 LLM 综合, 避免真实 API 调用.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class TestSearchFallback:
    """Search Grounding fallback (doubao_search + LLM 综合)."""

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.web_research.search_fallback.synthesize_with_llm",
        new_callable=AsyncMock,
    )
    @patch(
        "src.tools.experts.web_research.search_fallback.doubao_web_search",
        new_callable=AsyncMock,
    )
    async def test_success_returns_synthesized_result(
        self, mock_search, mock_synthesize
    ):
        """检索+综合成功, 返回标准结果, 来源含域名."""
        from src.tools.experts.web_research.search_fallback import search_fallback

        mock_search.return_value = {
            "search_results": [
                {"title": "结果1", "link": "https://a.com/x", "content": "内容1"},
                {"title": "结果2", "link": "https://b.org/y", "content": "内容2"},
            ]
        }
        mock_synthesize.return_value = "综合答案"

        result = await search_fallback("测试查询", language="zh")

        assert "error" not in result
        assert result["result"].startswith("综合答案")
        assert "来源: a.com" in result["result"]
        assert "来源: b.org" in result["result"]
        assert result["tools_used"] == ["doubao_search", "llm_synthesis"]
        assert result["depth"] == "quick"

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.web_research.search_fallback.doubao_web_search",
        new_callable=AsyncMock,
    )
    async def test_search_error_returns_error(self, mock_search):
        """检索失败时返回 error."""
        from src.tools.experts.web_research.search_fallback import search_fallback

        mock_search.return_value = {"error": "API 错误"}

        result = await search_fallback("测试查询")

        assert "error" in result
        assert "API 错误" in result["error"]

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.web_research.search_fallback.synthesize_with_llm",
        new_callable=AsyncMock,
    )
    @patch(
        "src.tools.experts.web_research.search_fallback.doubao_web_search",
        new_callable=AsyncMock,
    )
    async def test_empty_results_returns_error(self, mock_search, mock_synthesize):
        """检索无结果时返回 error, 不调用综合."""
        from src.tools.experts.web_research.search_fallback import search_fallback

        mock_search.return_value = {"search_results": []}

        result = await search_fallback("测试查询")

        assert "error" in result
        mock_synthesize.assert_not_called()

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.web_research.search_fallback.synthesize_with_llm",
        new_callable=AsyncMock,
    )
    @patch(
        "src.tools.experts.web_research.search_fallback.doubao_web_search",
        new_callable=AsyncMock,
    )
    async def test_synthesize_failure_returns_error(self, mock_search, mock_synthesize):
        """综合失败时返回 error."""
        from src.tools.experts.web_research.search_fallback import search_fallback

        mock_search.return_value = {
            "search_results": [{"title": "t", "link": "https://a.com", "content": "c"}]
        }
        mock_synthesize.side_effect = RuntimeError("LLM 异常")

        result = await search_fallback("测试查询")

        assert "error" in result
        assert "综合失败" in result["error"]


class TestUrlContextFallback:
    """URL Context fallback (zhipu_reader + LLM 综合)."""

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.web_research.url_context_fallback.synthesize_with_llm",
        new_callable=AsyncMock,
    )
    @patch(
        "src.tools.experts.web_research.url_context_fallback.zhipu_web_reader",
        new_callable=AsyncMock,
    )
    async def test_success_returns_synthesized_result(
        self, mock_reader, mock_synthesize
    ):
        """抓取+综合成功, 返回标准结果, 含来源链接."""
        from src.tools.experts.web_research.url_context_fallback import (
            url_context_fallback,
        )

        mock_reader.return_value = {"content": "页面正文", "title": "页面标题"}
        mock_synthesize.return_value = "综合答案"

        result = await url_context_fallback("查询", ["https://a.com"], language="zh")

        assert "error" not in result
        assert result["result"].startswith("综合答案")
        assert "https://a.com" in result["result"]
        assert result["tools_used"] == ["zhipu_reader", "llm_synthesis"]

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.web_research.url_context_fallback.zhipu_web_reader",
        new_callable=AsyncMock,
    )
    async def test_all_urls_failed_returns_error(self, mock_reader):
        """所有 URL 抓取失败时返回 error."""
        from src.tools.experts.web_research.url_context_fallback import (
            url_context_fallback,
        )

        mock_reader.return_value = {"error": "抓取失败"}

        result = await url_context_fallback("查询", ["https://a.com"])

        assert "error" in result
        assert "所有 URL 抓取失败" in result["error"]

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.web_research.url_context_fallback.synthesize_with_llm",
        new_callable=AsyncMock,
    )
    @patch(
        "src.tools.experts.web_research.url_context_fallback.zhipu_web_reader",
        new_callable=AsyncMock,
    )
    async def test_partial_failure_continues(self, mock_reader, mock_synthesize):
        """部分 URL 失败时仍基于成功的结果综合."""
        from src.tools.experts.web_research.url_context_fallback import (
            url_context_fallback,
        )

        mock_reader.side_effect = [
            {"error": "失败"},
            {"content": "正文", "title": "成功"},
        ]
        mock_synthesize.return_value = "综合答案"

        result = await url_context_fallback("查询", ["https://a.com", "https://b.com"])

        assert "error" not in result
        assert result["result"].startswith("综合答案")
        # 只有成功的 b.com 出现在来源
        assert "https://b.com" in result["result"]
        assert "https://a.com" not in result["result"]

    @pytest.mark.asyncio
    async def test_empty_urls_returns_error(self):
        """空 URL 列表返回 error."""
        from src.tools.experts.web_research.url_context_fallback import (
            url_context_fallback,
        )

        result = await url_context_fallback("查询", [])

        assert "error" in result


class TestMapsFallback:
    """Maps Grounding fallback (place_search + LLM 综合)."""

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.geo_research.maps_fallback.synthesize_with_llm",
        new_callable=AsyncMock,
    )
    @patch(
        "src.tools.experts.geo_research.unified_geo_client.place_search",
        new_callable=AsyncMock,
    )
    async def test_success_returns_synthesized_result(
        self, mock_place_search, mock_synthesize
    ):
        """检索+综合成功, 返回标准结果, 含地点名."""
        from src.tools.experts.geo_research.maps_fallback import maps_fallback

        mock_place_search.return_value = {
            "places": [
                {
                    "name": "餐厅A",
                    "address": "路1号",
                    "category": "餐饮",
                    "lat": 1.0,
                    "lng": 2.0,
                }
            ]
        }
        mock_synthesize.return_value = "综合答案"

        result = await maps_fallback("附近的餐厅", language="zh")

        assert "error" not in result
        assert result["result"].startswith("综合答案")
        assert "餐厅A" in result["result"]
        assert result["tools_used"] == ["place_search", "llm_synthesis"]

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.geo_research.unified_geo_client.place_search",
        new_callable=AsyncMock,
    )
    async def test_search_error_returns_error(self, mock_place_search):
        """检索失败时返回 error."""
        from src.tools.experts.geo_research.maps_fallback import maps_fallback

        mock_place_search.return_value = {"error": "地图错误"}

        result = await maps_fallback("查询")

        assert "error" in result
        assert "地图错误" in result["error"]

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.geo_research.unified_geo_client.place_search",
        new_callable=AsyncMock,
    )
    async def test_empty_places_returns_error(self, mock_place_search):
        """检索无结果时返回 error."""
        from src.tools.experts.geo_research.maps_fallback import maps_fallback

        mock_place_search.return_value = {"places": []}

        result = await maps_fallback("查询")

        assert "error" in result
