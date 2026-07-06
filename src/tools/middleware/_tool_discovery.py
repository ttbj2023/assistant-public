"""工具发现中间件 - 运行时动态注入休眠工具."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class ToolDiscoveryMiddleware(AgentMiddleware):
    """工具发现中间件 - 运行时动态注入休眠工具.

    Args:
        dormant_tools: 休眠工具池, 这些工具不会出现在初始工具列表中,
                       只能通过search_available_tools发现后激活
        group_members_map: 工具组名 -> 成员工具名映射. search命中组名时,
                           展开为整组成员激活(组对主对话模型透明).

    """

    def __init__(
        self,
        dormant_tools: list[BaseTool],
        group_members_map: dict[str, list[str]] | None = None,
    ) -> None:
        self._dormant_tools: dict[str, BaseTool] = {t.name: t for t in dormant_tools}
        self._activated_tools: set[str] = set()
        self._group_members_map: dict[str, list[str]] = group_members_map or {}

        logger.info(
            f"ToolDiscoveryMiddleware初始化: "
            f"{len(self._dormant_tools)} 个休眠工具 "
            f"({list(self._dormant_tools.keys())})",
        )

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Any,
    ) -> Any:
        """拦截模型调用, 检测search_available_tools调用并注入激活的工具.

        流程:
        1. 检查消息历史中是否有search_available_tools的调用结果
        2. 如果有, 从结果中提取匹配的工具名并激活
        3. 将已激活的工具注入到当前模型调用的工具列表中
        4. 调用handler执行模型

        Args:
            request: 模型请求
            handler: 模型执行回调

        Returns:
            模型响应

        """
        self._check_and_activate(request)

        if not self._activated_tools:
            return await handler(request)

        activated = [
            self._dormant_tools[name]
            for name in self._activated_tools
            if name in self._dormant_tools
        ]

        current_tools = list(request.tools) if request.tools else []

        existing_names = {t.name for t in current_tools} if current_tools else set()
        new_tools = [t for t in activated if t.name not in existing_names]

        if not new_tools:
            return await handler(request)

        all_tools = current_tools + new_tools
        modified_request = request.override(tools=all_tools)

        logger.info(
            f"🔧 动态注入 {len(new_tools)} 个工具: "
            f"{[t.name for t in new_tools]}, "
            f"总工具数: {len(all_tools)}",
        )

        return await handler(modified_request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        """拦截工具调用, 为动态注入的休眠工具提供正确的BaseTool实例.

        LangChain factory要求中间件实现awrap_tool_call才能合法注入动态工具:
        1. factory的_get_bound_model会检测此方法是否存在, 存在则跳过未知工具验证
        2. 当LLM调用动态注入的休眠工具时, 此方法将请求路由到正确的工具实例

        Args:
            request: 工具调用请求
            handler: 工具执行回调

        Returns:
            工具执行结果

        """
        tool_name = request.tool_call.get("name", "")
        if tool_name in self._dormant_tools:
            request = request.override(tool=self._dormant_tools[tool_name])
            logger.info("🔧 中间件路由休眠工具调用: %s", tool_name)
        return await handler(request)

    def _check_and_activate(self, request: ModelRequest) -> None:
        """检查消息历史中的search_available_tools调用结果, 激活匹配工具.

        扫描消息历史, 查找search_available_tools的ToolMessage结果,
        解析其中matched_tools列表并激活对应的工具.

        Args:
            request: 模型请求, 包含消息历史

        """
        messages = request.state.get("messages", [])
        if not messages:
            return

        for msg in reversed(messages):
            if not isinstance(msg, ToolMessage):
                if isinstance(msg, AIMessage):
                    break
                continue

            if msg.name != "search_available_tools":
                continue

            matched = self._parse_matched_tools(msg.content)
            for matched_name in matched:
                # 组名展开为成员, 非组名保持单元素
                names_to_activate = self._group_members_map.get(matched_name)
                if names_to_activate is None:
                    names_to_activate = [matched_name]
                for tool_name in names_to_activate:
                    if (
                        tool_name in self._dormant_tools
                        and tool_name not in self._activated_tools
                    ):
                        self._activated_tools.add(tool_name)
                        logger.info("🔧 激活休眠工具: %s", tool_name)

    @staticmethod
    def _parse_matched_tools(content: Any) -> list[str]:
        """从search_available_tools的返回结果中解析匹配的工具名.

        Args:
            content: ToolMessage的内容

        Returns:
            匹配的工具名列表

        """
        if isinstance(content, str):
            try:
                data = json.loads(content)
            except (json.JSONDecodeError, ValueError):
                return []
        elif isinstance(content, dict):
            data = content
        else:
            return []

        matched = data.get("matched_tools", [])
        return [t["name"] for t in matched if isinstance(t, dict) and "name" in t]
