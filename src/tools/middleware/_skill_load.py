"""Skill加载中间件 - 运行时动态注入skill关联工具(渐进式披露).

复刻ToolDiscoveryMiddleware的同构模式:
- load_skill(常驻) ↔ search_available_tools(常驻)
- per-skill关联工具池 ↔ dormant_tools(休眠工具池)
- awrap_model_call扫描load_skill的ToolMessage激活 ↔ 扫描search_available_tools
- awrap_tool_call路由关联工具调用 ↔ 路由休眠工具

LLM调用load_skill(xxx)后, 本中间件检测到调用结果(ToolMessage),
从AIMessage的tool_calls中提取skill_name, 激活该skill的关联工具,
通过request.override(tools=...)注入后续模型调用.

per-skill映射: 不同skill的关联工具互不影响.
load_skill("xlsx") → 注入skill_executor; load_skill("chart_maker") → 注入3个图表工具.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class SkillLoadMiddleware(AgentMiddleware):
    """Skill加载中间件 - 运行时动态注入skill关联工具.

    Args:
        skill_tool_map: per-skill关联工具映射 {skill_name: [tool, ...]},
                       这些工具不出现在初始工具列表, 只能通过load_skill激活后注入.
                      不同skill的工具互相隔离, load_skill("A")只注入A的工具.

    """

    def __init__(self, skill_tool_map: dict[str, list[BaseTool]]) -> None:
        self._skill_tool_map = skill_tool_map
        # 扁平化所有可注入工具, 供awrap_tool_call路由
        self._all_injectable: dict[str, BaseTool] = {}
        for tools in skill_tool_map.values():
            for t in tools:
                self._all_injectable[t.name] = t
        self._activated_names: set[str] = set()

        total = sum(len(v) for v in skill_tool_map.values())
        tool_summary = {k: [t.name for t in v] for k, v in skill_tool_map.items()}
        logger.info(
            "SkillLoadMiddleware初始化: %d个skill, %d个可注入工具 (%s)",
            len(skill_tool_map),
            total,
            tool_summary,
        )

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Any,
    ) -> Any:
        """拦截模型调用, 检测load_skill调用并注入激活的关联工具.

        流程:
        1. 检查消息历史中是否有load_skill的调用结果(ToolMessage)
        2. 从对应AIMessage提取skill_name, 激活该skill的关联工具
        3. 将已激活的工具注入到当前模型调用的工具列表中
        4. 调用handler执行模型

        Args:
            request: 模型请求
            handler: 模型执行回调

        Returns:
            模型响应

        """
        self._check_and_activate(request)

        if not self._activated_names:
            return await handler(request)

        activated = [
            self._all_injectable[name]
            for name in self._activated_names
            if name in self._all_injectable
        ]

        current_tools = list(request.tools) if request.tools else []
        existing_names = {t.name for t in current_tools} if current_tools else set()
        new_tools = [t for t in activated if t.name not in existing_names]

        if not new_tools:
            return await handler(request)

        all_tools = current_tools + new_tools
        modified_request = request.override(tools=all_tools)

        logger.info(
            "🔧 动态注入 %d 个skill关联工具: %s, 总工具数: %d",
            len(new_tools),
            [t.name for t in new_tools],
            len(all_tools),
        )

        return await handler(modified_request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        """拦截工具调用, 为动态注入的关联工具提供正确的BaseTool实例.

        LangChain factory要求中间件实现awrap_tool_call才能合法注入动态工具:
        1. factory的_get_bound_model会检测此方法是否存在, 存在则跳过未知工具验证
        2. 当LLM调用动态注入的工具时, 此方法将请求路由到正确的工具实例

        Args:
            request: 工具调用请求
            handler: 工具执行回调

        Returns:
            工具执行结果

        """
        tool_name = request.tool_call.get("name", "")
        if tool_name in self._all_injectable:
            request = request.override(tool=self._all_injectable[tool_name])
            logger.info("🔧 中间件路由skill关联工具调用: %s", tool_name)
        return await handler(request)

    def _check_and_activate(self, request: ModelRequest) -> None:
        """检查消息历史中的load_skill调用结果, 激活对应skill的关联工具.

        扫描消息历史, 查找load_skill的ToolMessage. 从前一条AIMessage的tool_calls
        提取skill_name, 只激活该skill的关联工具(per-skill隔离).

        Args:
            request: 模型请求, 包含消息历史

        """
        messages = request.state.get("messages", [])
        if not messages:
            return

        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if isinstance(msg, AIMessage):
                break
            if not isinstance(msg, ToolMessage) or msg.name != "load_skill":
                continue

            # 从前一条AIMessage提取skill_name
            skill_name = self._extract_skill_name(messages, i)
            if not skill_name:
                continue
            self._activate_skill(skill_name)

    @staticmethod
    def _extract_skill_name(messages: list, tool_msg_index: int) -> str | None:
        """从load_skill ToolMessage前方的AIMessage中提取skill_name参数.

        Args:
            messages: 消息历史
            tool_msg_index: load_skill ToolMessage的索引

        Returns:
            skill_name; 无法提取时返回None

        """
        if tool_msg_index == 0:
            return None
        prev = messages[tool_msg_index - 1]
        if not isinstance(prev, AIMessage):
            return None
        for tc in prev.tool_calls:
            if tc.get("name") == "load_skill":
                return tc.get("args", {}).get("skill_name")
        return None

    def _activate_skill(self, skill_name: str) -> None:
        """激活指定skill的关联工具(已激活的跳过).

        Args:
            skill_name: skill名称

        """
        tools = self._skill_tool_map.get(skill_name)
        if not tools:
            return
        for tool in tools:
            if tool.name not in self._activated_names:
                self._activated_names.add(tool.name)
                logger.info(
                    "🔧 激活skill关联工具: %s (skill=%s)",
                    tool.name,
                    skill_name,
                )
