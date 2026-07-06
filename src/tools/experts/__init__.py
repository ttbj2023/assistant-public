"""专家工具模块 - 全局共享的专业化研究工具.

专家工具继承 BaseExpertTool, 全局共享实例(不区分用户), 内部编排子Agent完成复杂任务.

## 工具体系分类
- **内部工具** (BaseInternalTool): 需要数据库/用户隔离, 每用户实例
- **外部工具** (BaseExternalTool): 无状态全局共享, 直接调用外部API
- **专家工具** (BaseExpertTool): 无数据库, 全局共享, 内部编排子Agent
- **MCP工具**: 通过McpBridge接入的外部协议工具

## 核心工具
- **WebResearchTool**: 网络研究工具(自主搜索+抓取+综合分析)
- **GeoResearchTool**: 地理出行研究工具(封装百度地图API)
"""

from __future__ import annotations

from src.tools.shared.base_expert_tool import BaseExpertTool

from .expert_factory import EXPERT_TOOL_NAMES, create_expert_tools
from .geo_research_tool import GeoResearchTool
from .web_research_tool import WebResearchTool

__all__ = [
    "EXPERT_TOOL_NAMES",
    "BaseExpertTool",
    "GeoResearchTool",
    "WebResearchTool",
    "create_expert_tools",
]
