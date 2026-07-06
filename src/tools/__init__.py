"""工具模块 - 极简工具架构

提供基于真实业务需求的工具管理接口, 支持智能缓存策略.

## 核心接口
- **ToolsManager**: 极简工具管理器, 专注Agent和API模块的真实需求
  - create_tools(): 创建工具集(Agent模块核心需求)
  - health_check(): 工具健康检查(API模块核心需求)

## 内部工具 (需要用户隔离)
- **CreateTodoTool/ListTodosTool/UpdateTodoTool/DeleteTodoTool**: TODO任务管理(打包成 todo_manager_group)
- **AsyncMemoryRetrievalTool**: 异步记忆检索工具

## MCP工具 (全局共享, 无状态)
- 通过fastmcp.Client集成, 支持streamable_http/sse/stdio传输
- 配置驱动: 通过config.yaml的mcp_servers配置管理

**缓存策略**:
- 内部工具: 按用户-线程-Agent三级隔离缓存
- MCP工具: 全局共享, 由McpToolManager管理

**配置管理**:
- 所有工具配置统一通过 config.yaml + src/config/tools_config.py 管理
"""

from __future__ import annotations

from .internal import (
    AsyncMemoryRetrievalTool,
    CreateTodoTool,
    DeleteTodoTool,
    ListTodosTool,
    UpdateTodoTool,
)
from .tools_manager import ToolsManager, get_tools_manager

__all__ = [
    "AsyncMemoryRetrievalTool",
    "CreateTodoTool",
    "DeleteTodoTool",
    "ListTodosTool",
    "ToolsManager",
    "UpdateTodoTool",
    "get_tools_manager",
]
