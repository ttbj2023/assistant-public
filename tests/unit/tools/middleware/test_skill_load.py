"""SkillLoadMiddleware 单元测试.

覆盖范围:
- 初始化: per-skill工具映射, _all_injectable扁平化, activated初始状态
- awrap_tool_call: 关联工具路由
- awrap_model_call: 动态工具注入
- _check_and_activate: load_skill ToolMessage扫描 + skill_name提取 + per-skill隔离
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware import ModelRequest, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool

from src.tools.middleware._skill_load import SkillLoadMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(name: str) -> MagicMock:
    tool = MagicMock(spec=BaseTool)
    tool.name = name
    return tool


def _make_model_request(
    messages: list | None = None,
    tools: list | None = None,
) -> ModelRequest:
    return ModelRequest(
        model=MagicMock(),
        messages=[],
        system_message=None,
        tool_choice=None,
        tools=tools or [],
        response_format=None,
        state={"messages": messages or []},
        runtime=MagicMock(),
        model_settings={},
    )


def _make_ai_message_with_load_skill(skill_name: str) -> AIMessage:
    """创建包含load_skill tool_call的AIMessage."""
    return AIMessage(
        content="",
        tool_calls=[{
            "name": "load_skill",
            "args": {"skill_name": skill_name},
            "id": "tc_load",
            "type": "tool_call",
        }],
    )


def _make_load_skill_tool_message(skill_name: str = "xlsx") -> ToolMessage:
    """创建load_skill的ToolMessage(模拟工具返回结果)."""
    return ToolMessage(
        content="L2正文",
        tool_call_id="tc_load",
        name="load_skill",
    )


def _make_tool_call_request(
    tool_name: str, tool: BaseTool | None = None
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": tool_name, "args": {}, "id": "tc_123"},
        tool=tool,
        state={},
        runtime=MagicMock(),
    )


# ---------------------------------------------------------------------------
# TestAwrapToolCall
# ---------------------------------------------------------------------------


class TestAwrapToolCall:
    @pytest.mark.asyncio
    async def test_should_route_injectable_tool(self) -> None:
        executor = _make_tool("skill_executor")
        mw = SkillLoadMiddleware({"xlsx": [executor]})
        handler = AsyncMock(return_value=MagicMock())
        request = _make_tool_call_request("skill_executor")
        await mw.awrap_tool_call(request, handler)
        handler.assert_called_once()
        called = handler.call_args[0][0]
        assert called.tool is executor

    @pytest.mark.asyncio
    async def test_should_pass_through_non_injectable_tool(self) -> None:
        executor = _make_tool("skill_executor")
        mw = SkillLoadMiddleware({"xlsx": [executor]})
        handler = AsyncMock(return_value=MagicMock())
        other = _make_tool("other")
        request = _make_tool_call_request("other", tool=other)
        await mw.awrap_tool_call(request, handler)
        assert request.tool is other


# ---------------------------------------------------------------------------
# TestCheckAndActivate
# ---------------------------------------------------------------------------


class TestCheckAndActivate:
    def test_should_activate_on_load_skill_message(self) -> None:
        """load_skill ToolMessage + 前方AIMessage含skill_name → 激活该skill工具."""
        executor = _make_tool("skill_executor")
        mw = SkillLoadMiddleware({"xlsx": [executor]})
        messages = [
            _make_ai_message_with_load_skill("xlsx"),
            _make_load_skill_tool_message(),
        ]
        request = _make_model_request(messages=messages)
        mw._check_and_activate(request)
        assert "skill_executor" in mw._activated_names

    def test_should_stop_at_ai_message(self) -> None:
        """AIMessage之后的load_skill(上一轮)不重复处理."""
        executor = _make_tool("skill_executor")
        mw = SkillLoadMiddleware({"xlsx": [executor]})
        messages = [
            _make_load_skill_tool_message(),
            AIMessage(content="思考"),
        ]
        request = _make_model_request(messages=messages)
        mw._check_and_activate(request)
        assert mw._activated_names == set()

    def test_should_skip_other_tool_messages(self) -> None:
        executor = _make_tool("skill_executor")
        mw = SkillLoadMiddleware({"xlsx": [executor]})
        other_msg = ToolMessage(content="x", tool_call_id="tc", name="other_tool")
        request = _make_model_request(messages=[other_msg])
        mw._check_and_activate(request)
        assert mw._activated_names == set()

    def test_empty_messages_should_not_activate(self) -> None:
        executor = _make_tool("skill_executor")
        mw = SkillLoadMiddleware({"xlsx": [executor]})
        request = _make_model_request(messages=[])
        mw._check_and_activate(request)
        assert mw._activated_names == set()

    def test_empty_map_should_not_activate(self) -> None:
        mw = SkillLoadMiddleware({})
        messages = [
            _make_ai_message_with_load_skill("xlsx"),
            _make_load_skill_tool_message(),
        ]
        request = _make_model_request(messages=messages)
        mw._check_and_activate(request)
        assert mw._activated_names == set()

    def test_per_skill_isolation(self) -> None:
        """load xlsx只激活xlsx的工具, 不激活chart_maker的."""
        executor = _make_tool("skill_executor")
        mermaid = _make_tool("mermaid_chart")
        mw = SkillLoadMiddleware({
            "xlsx": [executor],
            "chart_maker": [mermaid],
        })
        messages = [
            _make_ai_message_with_load_skill("xlsx"),
            _make_load_skill_tool_message(),
        ]
        request = _make_model_request(messages=messages)
        mw._check_and_activate(request)
        assert "skill_executor" in mw._activated_names
        assert "mermaid_chart" not in mw._activated_names

    def test_multiple_skill_loads_activate_all(self) -> None:
        """先load xlsx再load chart_maker → 两组工具都激活."""
        executor = _make_tool("skill_executor")
        mermaid = _make_tool("mermaid_chart")
        mw = SkillLoadMiddleware({
            "xlsx": [executor],
            "chart_maker": [mermaid],
        })
        # 第一轮: load xlsx
        mw._activate_skill("xlsx")
        # 第二轮: load chart_maker
        mw._activate_skill("chart_maker")
        assert "skill_executor" in mw._activated_names
        assert "mermaid_chart" in mw._activated_names

    def test_should_not_activate_unknown_skill(self) -> None:
        """load_skill("nope") → 无关联工具, 不激活."""
        executor = _make_tool("skill_executor")
        mw = SkillLoadMiddleware({"xlsx": [executor]})
        messages = [
            _make_ai_message_with_load_skill("nope"),
            _make_load_skill_tool_message(),
        ]
        request = _make_model_request(messages=messages)
        mw._check_and_activate(request)
        assert mw._activated_names == set()


# ---------------------------------------------------------------------------
# TestAwrapModelCall
# ---------------------------------------------------------------------------


class TestAwrapModelCall:
    @pytest.mark.asyncio
    async def test_no_activated_should_pass_through(self) -> None:
        executor = _make_tool("skill_executor")
        mw = SkillLoadMiddleware({"xlsx": [executor]})
        handler = AsyncMock(return_value=MagicMock())
        request = _make_model_request()
        await mw.awrap_model_call(request, handler)
        handler.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_activated_should_inject_into_request(self) -> None:
        executor = _make_tool("skill_executor")
        mw = SkillLoadMiddleware({"xlsx": [executor]})
        mw._activated_names.add("skill_executor")
        core = _make_tool("core_tool")
        request = _make_model_request(tools=[core])
        handler = AsyncMock(return_value=MagicMock())
        await mw.awrap_model_call(request, handler)
        called = handler.call_args[0][0]
        names = {t.name for t in called.tools}
        assert "skill_executor" in names
        assert "core_tool" in names

    @pytest.mark.asyncio
    async def test_should_activate_and_inject_from_history(self) -> None:
        """完整链路: load_skill消息 → 激活 → 注入工具."""
        executor = _make_tool("skill_executor")
        mw = SkillLoadMiddleware({"xlsx": [executor]})
        messages = [
            _make_ai_message_with_load_skill("xlsx"),
            _make_load_skill_tool_message(),
        ]
        request = _make_model_request(messages=messages)
        handler = AsyncMock(return_value=MagicMock())
        await mw.awrap_model_call(request, handler)
        called = handler.call_args[0][0]
        names = {t.name for t in called.tools}
        assert "skill_executor" in names
