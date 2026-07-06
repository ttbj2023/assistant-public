"""WebResearchTool / GeoResearchTool 包装层单元测试.

测试范围:
1. 工具初始化和属性
2. _arun的成功路径(Mock service层)
3. _arun的错误处理(Mock service层异常)
4. _arun的error字段处理(service返回error)

Mock策略: Mock run_web_research / run_geo_research函数, 避免真实Agent执行.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.experts.geo_research_tool import GeoResearchTool
from src.tools.experts.web_research_tool import WebResearchTool

# =============================================================================
# 1. WebResearchTool _arun 测试
# =============================================================================


class TestWebResearchToolArun:
    """测试WebResearchTool._arun执行"""

    @pytest.mark.asyncio
    async def test_should_return_result_on_success(self):
        tool = WebResearchTool()
        with patch(
            "src.tools.experts.web_research_tool.run_web_research",
            new_callable=AsyncMock,
            return_value={"result": "research answer"},
        ):
            result = await tool._arun("test query")
            assert result == "research answer"

    @pytest.mark.asyncio
    async def test_should_return_error_json_when_service_returns_error(self):
        tool = WebResearchTool()
        with patch(
            "src.tools.experts.web_research_tool.run_web_research",
            new_callable=AsyncMock,
            return_value={"error": "timeout", "result": "partial"},
        ):
            result = await tool._arun("test query")
            parsed = json.loads(result)
            assert parsed["error"] == "timeout"
            assert parsed["result"] == "partial"

    @pytest.mark.asyncio
    async def test_should_return_error_json_on_exception(self):
        tool = WebResearchTool()
        with patch(
            "src.tools.experts.web_research_tool.run_web_research",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection failed"),
        ):
            result = await tool._arun("test query")
            parsed = json.loads(result)
            assert "connection failed" in parsed["error"]
            assert parsed["source"] == "web_research"

    @pytest.mark.asyncio
    async def test_should_pass_all_params_to_service(self):
        tool = WebResearchTool(model_id="gemini:test", timeout=60.0)
        with patch(
            "src.tools.experts.web_research_tool.run_web_research",
            new_callable=AsyncMock,
            return_value={"result": "ok"},
        ) as mock_service:
            await tool._arun("query", depth="deep", language="en")
            mock_service.assert_called_once_with(
                query="query",
                depth="deep",
                language="en",
                model_id="gemini:test",
                timeout=60.0,
                llm_request_timeout=90.0,
                mcp_bridge=tool.mcp_bridge,
            )


# =============================================================================
# 2. GeoResearchTool _arun 测试
# =============================================================================


class TestGeoResearchToolArun:
    """测试GeoResearchTool._arun执行"""

    @pytest.mark.asyncio
    async def test_should_return_result_on_success(self):
        tool = GeoResearchTool()
        with patch(
            "src.tools.experts.geo_research_tool.run_geo_research",
            new_callable=AsyncMock,
            return_value={"result": "geo answer"},
        ):
            result = await tool._arun("test query")
            assert result == "geo answer"

    @pytest.mark.asyncio
    async def test_should_return_error_json_when_service_returns_error(self):
        tool = GeoResearchTool()
        with patch(
            "src.tools.experts.geo_research_tool.run_geo_research",
            new_callable=AsyncMock,
            return_value={"error": "baidu_api_unavailable", "result": "fallback"},
        ):
            result = await tool._arun("test query")
            parsed = json.loads(result)
            assert parsed["error"] == "baidu_api_unavailable"

    @pytest.mark.asyncio
    async def test_should_return_error_json_on_exception(self):
        tool = GeoResearchTool()
        with patch(
            "src.tools.experts.geo_research_tool.run_geo_research",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection failed"),
        ):
            result = await tool._arun("test query")
            parsed = json.loads(result)
            assert "connection failed" in parsed["error"]
            assert parsed["source"] == "geo_navigator"

    @pytest.mark.asyncio
    async def test_should_pass_all_params_to_service(self):
        tool = GeoResearchTool(model_id="openai:test", timeout=60.0)
        with patch(
            "src.tools.experts.geo_research_tool.run_geo_research",
            new_callable=AsyncMock,
            return_value={"result": "ok"},
        ) as mock_service:
            await tool._arun("query", depth="deep", language="en")
            mock_service.assert_called_once_with(
                query="query",
                depth="deep",
                language="en",
                model_id="openai:test",
                timeout=60.0,
            )
