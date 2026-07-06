"""ToolDiscoveryMiddleware 单元测试

覆盖范围:
- 初始化: dormant_tools 存储, activated_tools 初始状态
- awrap_tool_call: 休眠工具路由 (本次修复核心)
- awrap_model_call: 动态工具注入到模型请求
- _check_and_activate: 消息历史扫描激活
- _parse_matched_tools: JSON 解析 (静态方法)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware import ModelRequest, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool

from src.tools.middleware._tool_discovery import ToolDiscoveryMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dormant_tool(name: str) -> MagicMock:
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


def _make_tool_message(
    content: str, name: str = "search_available_tools"
) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="tc_123", name=name)


def _make_tool_call_request(
    tool_name: str, tool: BaseTool | None = None
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": tool_name, "args": {"query": "test"}, "id": "tc_123"},
        tool=tool,
        state={},
        runtime=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dormant_tools():
    return [_make_dormant_tool("web_research"), _make_dormant_tool("geo_navigator")]


@pytest.fixture
def middleware(dormant_tools):
    return ToolDiscoveryMiddleware(dormant_tools)


@pytest.fixture
def mock_handler():
    return AsyncMock(return_value=MagicMock())


# ---------------------------------------------------------------------------
# TestAwrapToolCall
# ---------------------------------------------------------------------------


class TestAwrapToolCall:
    @pytest.mark.asyncio
    async def test_should_route_dormant_tool_to_correct_instance(
        self, middleware, mock_handler
    ):
        request = _make_tool_call_request("web_research")
        await middleware.awrap_tool_call(request, mock_handler)
        mock_handler.assert_called_once()
        called_request = mock_handler.call_args[0][0]
        assert called_request.tool is middleware._dormant_tools["web_research"]

    @pytest.mark.asyncio
    async def test_should_pass_through_non_dormant_tool(self, middleware, mock_handler):
        some_tool = _make_dormant_tool("other_tool")
        request = _make_tool_call_request("other_tool", tool=some_tool)
        await middleware.awrap_tool_call(request, mock_handler)
        assert request.tool is some_tool

    @pytest.mark.asyncio
    async def test_should_call_handler_with_modified_request(
        self, middleware, mock_handler
    ):
        request = _make_tool_call_request("geo_navigator")
        await middleware.awrap_tool_call(request, mock_handler)
        mock_handler.assert_called_once()
        called_request = mock_handler.call_args[0][0]
        assert called_request.tool is middleware._dormant_tools["geo_navigator"]

    @pytest.mark.asyncio
    async def test_should_handle_empty_dormant_tools(self, mock_handler):
        mw = ToolDiscoveryMiddleware([])
        request = _make_tool_call_request("any_tool")
        await mw.awrap_tool_call(request, mock_handler)
        mock_handler.assert_called_once()


# ---------------------------------------------------------------------------
# TestAwrapModelCall
# ---------------------------------------------------------------------------


class TestAwrapModelCall:
    @pytest.mark.asyncio
    async def test_no_activated_tools_should_pass_through(
        self, middleware, mock_handler
    ):
        request = _make_model_request()
        await middleware.awrap_model_call(request, mock_handler)
        mock_handler.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_activated_tools_should_inject_into_request(
        self, middleware, mock_handler
    ):
        middleware._activated_tools.add("web_research")
        core_tool = _make_dormant_tool("core_tool")
        request = _make_model_request(tools=[core_tool])
        await middleware.awrap_model_call(request, mock_handler)
        mock_handler.assert_called_once()
        called_request = mock_handler.call_args[0][0]
        names = {t.name for t in called_request.tools}
        assert "web_research" in names
        assert "core_tool" in names

    @pytest.mark.asyncio
    async def test_already_injected_tools_should_not_duplicate(
        self, middleware, mock_handler
    ):
        web_tool = middleware._dormant_tools["web_research"]
        middleware._activated_tools.add("web_research")
        request = _make_model_request(tools=[web_tool])
        await middleware.awrap_model_call(request, mock_handler)
        called_request = mock_handler.call_args[0][0]
        names = [t.name for t in called_request.tools]
        assert names.count("web_research") == 1

    @pytest.mark.asyncio
    async def test_multiple_activations_should_inject_all(
        self, middleware, mock_handler
    ):
        middleware._activated_tools.update(["web_research", "geo_navigator"])
        request = _make_model_request()
        await middleware.awrap_model_call(request, mock_handler)
        called_request = mock_handler.call_args[0][0]
        names = {t.name for t in called_request.tools}
        assert "web_research" in names
        assert "geo_navigator" in names

    @pytest.mark.asyncio
    async def test_should_activate_from_message_history(self, middleware, mock_handler):
        content = json.dumps({
            "success": True,
            "matched_tools": [{"name": "geo_navigator"}],
        })
        tool_msg = _make_tool_message(content)
        request = _make_model_request(messages=[tool_msg])
        await middleware.awrap_model_call(request, mock_handler)
        called_request = mock_handler.call_args[0][0]
        names = {t.name for t in called_request.tools}
        assert "geo_navigator" in names


# ---------------------------------------------------------------------------
# TestCheckAndActivate
# ---------------------------------------------------------------------------


class TestCheckAndActivate:
    def test_should_activate_from_tool_message(self, middleware):
        content = json.dumps({
            "success": True,
            "matched_tools": [{"name": "web_research"}],
        })
        tool_msg = _make_tool_message(content)
        request = _make_model_request(messages=[tool_msg])
        middleware._check_and_activate(request)
        assert "web_research" in middleware._activated_tools

    def test_should_stop_at_ai_message(self, middleware):
        content = json.dumps({
            "success": True,
            "matched_tools": [{"name": "web_research"}],
        })
        tool_msg = _make_tool_message(content)
        ai_msg = AIMessage(content="思考中")
        request = _make_model_request(messages=[tool_msg, ai_msg])
        middleware._check_and_activate(request)
        assert "web_research" not in middleware._activated_tools

    def test_should_skip_other_tool_messages(self, middleware):
        content = json.dumps({
            "success": True,
            "matched_tools": [{"name": "web_research"}],
        })
        tool_msg = _make_tool_message(content, name="other_tool")
        request = _make_model_request(messages=[tool_msg])
        middleware._check_and_activate(request)
        assert middleware._activated_tools == set()

    def test_should_not_activate_unknown_tools(self, middleware):
        content = json.dumps({
            "success": True,
            "matched_tools": [{"name": "unknown_tool"}],
        })
        tool_msg = _make_tool_message(content)
        request = _make_model_request(messages=[tool_msg])
        middleware._check_and_activate(request)
        assert middleware._activated_tools == set()

    def test_should_not_reactivate_already_activated(self, middleware):
        middleware._activated_tools.add("web_research")
        content = json.dumps({
            "success": True,
            "matched_tools": [{"name": "web_research"}],
        })
        tool_msg = _make_tool_message(content)
        request = _make_model_request(messages=[tool_msg])
        middleware._check_and_activate(request)
        assert len(middleware._activated_tools) == 1

    def test_empty_messages_should_not_activate(self, middleware):
        request = _make_model_request(messages=[])
        middleware._check_and_activate(request)
        assert middleware._activated_tools == set()

    def test_should_expand_group_name_to_members(self):
        """search命中组名时应展开为整组成员激活."""
        dormant = [
            _make_dormant_tool("schedule_message"),
            _make_dormant_tool("list_scheduled_messages"),
            _make_dormant_tool("cancel_scheduled_message"),
        ]
        mw = ToolDiscoveryMiddleware(
            dormant,
            group_members_map={
                "scheduled_messenger_group": [
                    "schedule_message",
                    "list_scheduled_messages",
                    "cancel_scheduled_message",
                ],
            },
        )
        content = json.dumps({
            "matched_tools": [{"name": "scheduled_messenger_group"}],
        })
        tool_msg = _make_tool_message(content)
        request = _make_model_request(messages=[tool_msg])
        mw._check_and_activate(request)
        assert mw._activated_tools == {
            "schedule_message",
            "list_scheduled_messages",
            "cancel_scheduled_message",
        }

    def test_should_not_activate_group_name_itself(self):
        """组名本身不应被激活(组对LLM透明, 组名不在dormant池)."""
        dormant = [_make_dormant_tool("a_tool"), _make_dormant_tool("b_tool")]
        mw = ToolDiscoveryMiddleware(
            dormant,
            group_members_map={"g1": ["a_tool", "b_tool"]},
        )
        content = json.dumps({"matched_tools": [{"name": "g1"}]})
        tool_msg = _make_tool_message(content)
        request = _make_model_request(messages=[tool_msg])
        mw._check_and_activate(request)
        assert "g1" not in mw._activated_tools
        assert mw._activated_tools == {"a_tool", "b_tool"}

    def test_mixed_group_and_plain_tool_activation(self):
        """search同时返回组名和普通工具名时都应正确激活."""
        dormant = [
            _make_dormant_tool("a"),
            _make_dormant_tool("b"),
            _make_dormant_tool("weather"),
        ]
        mw = ToolDiscoveryMiddleware(
            dormant,
            group_members_map={"g1": ["a", "b"]},
        )
        content = json.dumps({
            "matched_tools": [{"name": "g1"}, {"name": "weather"}],
        })
        tool_msg = _make_tool_message(content)
        request = _make_model_request(messages=[tool_msg])
        mw._check_and_activate(request)
        assert mw._activated_tools == {"a", "b", "weather"}

    def test_group_members_map_defaults_to_empty(self, dormant_tools):
        """不传 group_members_map 时默认空dict(向后兼容)."""
        mw = ToolDiscoveryMiddleware(dormant_tools)
        assert mw._group_members_map == {}


# ---------------------------------------------------------------------------
# TestParseMatchedTools
# ---------------------------------------------------------------------------


class TestParseMatchedTools:
    def test_should_parse_valid_json_string(self):
        content = json.dumps({"matched_tools": [{"name": "web_research"}]})
        result = ToolDiscoveryMiddleware._parse_matched_tools(content)
        assert result == ["web_research"]

    def test_should_parse_dict_content(self):
        content = {
            "matched_tools": [{"name": "geo_navigator"}, {"name": "web_research"}]
        }
        result = ToolDiscoveryMiddleware._parse_matched_tools(content)
        assert result == ["geo_navigator", "web_research"]

    def test_should_return_empty_for_invalid_json(self):
        result = ToolDiscoveryMiddleware._parse_matched_tools("not json at all")
        assert result == []

    def test_should_return_empty_for_non_dict_non_string(self):
        assert ToolDiscoveryMiddleware._parse_matched_tools(42) == []
        assert ToolDiscoveryMiddleware._parse_matched_tools([1, 2]) == []
        assert ToolDiscoveryMiddleware._parse_matched_tools(None) == []

    def test_should_skip_entries_without_name(self):
        content = {"matched_tools": [{"name": "a"}, {"id": "b"}]}
        result = ToolDiscoveryMiddleware._parse_matched_tools(content)
        assert result == ["a"]

    def test_should_handle_empty_matched_tools(self):
        content = {"matched_tools": []}
        result = ToolDiscoveryMiddleware._parse_matched_tools(content)
        assert result == []

    def test_should_handle_missing_matched_tools_key(self):
        content = {"success": True}
        result = ToolDiscoveryMiddleware._parse_matched_tools(content)
        assert result == []
