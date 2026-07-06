"""专家Agent共享工具函数 - 提取自多个专家Agent的通用逻辑."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


def extract_tool_calls(messages: list[Any]) -> list[str]:
    """从Agent执行结果的消息列表中提取所有工具调用名称."""
    tool_names: list[str] = []
    for msg in messages:
        if hasattr(msg, "tool_calls"):
            for tc in msg.tool_calls or []:
                name = getattr(tc, "name", "")
                if name:
                    tool_names.append(name)
        elif isinstance(msg, ToolMessage):
            name = getattr(msg, "name", "")
            if name:
                tool_names.append(name)
    return tool_names


def enable_tool_error_handling(agent: Any) -> None:
    """启用工具错误容错, 将工具异常转为ToolMessage返回给LLM.

    默认行为只捕获ToolInvocationError, 其他异常(如MCP ToolException)会直接崩溃Agent.
    设置handle_tool_errors=True后, 所有异常都会被转为错误ToolMessage,
    LLM可以根据错误信息调整回复策略.
    """
    try:
        tools_node = agent.nodes.get("tools")
        if tools_node and hasattr(tools_node, "bound"):
            tool_node = tools_node.bound
            if hasattr(tool_node, "_handle_tool_errors"):
                tool_node._handle_tool_errors = True
                logger.debug("已启用工具错误容错, 工具异常将转为ToolMessage")
    except Exception as e:
        logger.warning("启用工具错误容错失败(非致命): %s", e)
