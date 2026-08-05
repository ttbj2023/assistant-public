"""外部工具模块 - 无状态全局共享的外部服务封装.

外部工具直接继承 BaseTool + @sync_runnable, 无用户隔离, 直接调用外部API.
与MCP工具同属外部工具类别, 但通过直接API调用而非MCP协议接入.
"""

from __future__ import annotations

__all__: list[str] = []
