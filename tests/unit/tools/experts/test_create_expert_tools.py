"""create_expert_tools工厂函数 + EXPERT_TOOL_NAMES测试.

测试范围:
1. create_expert_tools - 工厂函数创建正确的工具实例
2. EXPERT_TOOL_NAMES - 常量验证
3. 工具属性 - model_id, mcp_bridge传递
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from src.tools.experts.expert_factory import create_expert_tools
from src.tools.experts.geo_research_tool import GeoResearchTool
from src.tools.experts.web_research_tool import WebResearchTool

# =============================================================================
# 1. create_expert_tools 工厂函数测试
# =============================================================================


class TestCreateExpertTools:
    """测试create_expert_tools工厂函数"""

    def test_should_create_web_research_tool(self):
        tools = create_expert_tools(["web_research"], model_id="test-model")
        assert len(tools) == 1
        assert isinstance(tools[0], WebResearchTool)

    def test_should_create_geo_research_tool(self):
        tools = create_expert_tools(["geo_navigator"], model_id="test-model")
        assert len(tools) == 1
        assert isinstance(tools[0], GeoResearchTool)

    def test_should_create_both_tools(self):
        tools = create_expert_tools(
            ["web_research", "geo_navigator"], model_id="test-model"
        )
        assert len(tools) == 2
        types = {type(t) for t in tools}
        assert types == {WebResearchTool, GeoResearchTool}

    def test_should_return_empty_list_for_empty_names(self):
        tools = create_expert_tools([], model_id="test-model")
        assert tools == []

    def test_should_skip_unknown_tool_names(self):
        tools = create_expert_tools(["unknown_tool"], model_id="test-model")
        assert tools == []

    def test_should_skip_unknown_and_create_known(self):
        tools = create_expert_tools(["unknown", "web_research"], model_id="test-model")
        assert len(tools) == 1
        assert isinstance(tools[0], WebResearchTool)

    def test_should_pass_model_id(self):
        tools = create_expert_tools(
            ["web_research"], model_id="gemini:gemini-2.0-flash"
        )
        assert tools[0].model_id == "gemini:gemini-2.0-flash"

    def test_geo_research_should_not_have_mcp_bridge(self):
        tools = create_expert_tools(["geo_navigator"], model_id="test-model")
        assert tools[0].mcp_bridge is None

    def test_all_tools_should_be_base_tool_instances(self):
        tools = create_expert_tools(
            ["web_research", "geo_navigator"], model_id="test-model"
        )
        for tool in tools:
            assert isinstance(tool, BaseTool)
