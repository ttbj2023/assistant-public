"""专家工具工厂 - 根据名称列表创建专家工具实例."""

from __future__ import annotations

from typing import Any

from src.tools.shared.base_expert_tool import BaseExpertTool

from .geo_research_tool import GeoResearchTool
from .web_research_tool import WebResearchTool

EXPERT_TOOL_NAMES = {"web_research", "geo_navigator"}


def create_expert_tools(
    tool_names: list[str],
    *,
    mcp_bridge: Any | None = None,
    model_id: str,
) -> list[BaseExpertTool]:
    """创建专家工具实例.

    Args:
        tool_names: 需要的专家工具名称列表
        mcp_bridge: McpBridge实例(用于获取MCP子工具)
        model_id: 专家工具使用的模型ID

    Returns:
        创建的BaseExpertTool实例列表

    """
    tools: list[BaseExpertTool] = []

    for name in tool_names:
        if name == "web_research":
            tools.append(
                WebResearchTool(
                    model_id=model_id,
                    mcp_bridge=mcp_bridge,
                ),
            )
        elif name == "geo_navigator":
            tools.append(
                GeoResearchTool(
                    model_id=model_id,
                ),
            )

    return tools
