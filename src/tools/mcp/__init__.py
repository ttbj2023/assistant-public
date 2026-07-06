"""MCP工具模块 - 基于fastmcp.Client的MCP协议集成.

提供基于fastmcp的MCP工具桥接, 支持通过MCP协议接入远程和本地工具服务.

核心组件:
- McpBridge: MCP工具桥接器, 封装fastmcp.Client实现Session复用和重试

特性:
- Session复用: Client实例常驻, 避免每次调用新建子进程/连接
- 内置重试: 失败后自动重连+限流感知等待
- 错误降级: 返回JSON错误字符串而非抛出异常

支持的传输协议:
- streamable_http: HTTP流式传输(如智谱AI搜索)
- sse: Server-Sent Events
- stdio: 本地子进程通信(如MiniMax搜索)
"""

from __future__ import annotations

from .mcp_tool_manager import McpBridge

__all__ = [
    "McpBridge",
]
