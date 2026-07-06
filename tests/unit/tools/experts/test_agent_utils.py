"""agent_utils单元测试 - 验证专家Agent共享工具函数.

测试范围:
1. extract_tool_calls - 从消息列表提取工具调用名称
2. enable_tool_error_handling - 启用工具错误容错
"""

from __future__ import annotations

from unittest.mock import Mock

from langchain_core.messages import HumanMessage, ToolMessage

from src.tools.experts.agent_utils import (
    enable_tool_error_handling,
    extract_tool_calls,
)

# =============================================================================
# 1. extract_tool_calls 测试
# =============================================================================


class TestExtractToolCalls:
    """测试从消息列表中提取工具调用名称"""

    def test_should_return_empty_list_for_empty_messages(self):
        result = extract_tool_calls([])
        assert result == []

    def test_should_extract_from_tool_message(self):
        msg = ToolMessage(content="result", name="search_tool", tool_call_id="tc1")
        result = extract_tool_calls([msg])
        assert result == ["search_tool"]

    def test_should_extract_from_ai_message_with_tool_calls(self):
        tool_call = Mock()
        tool_call.name = "fetch_url"
        msg = Mock()
        msg.tool_calls = [tool_call]
        result = extract_tool_calls([msg])
        assert result == ["fetch_url"]

    def test_should_extract_from_mixed_messages(self):
        tc1 = Mock()
        tc1.name = "tool_a"
        tc2 = Mock()
        tc2.name = "tool_b"
        ai_msg = Mock()
        ai_msg.tool_calls = [tc1, tc2]
        tool_msg = ToolMessage(content="ok", name="tool_c", tool_call_id="tc3")

        result = extract_tool_calls([ai_msg, tool_msg])
        assert result == ["tool_a", "tool_b", "tool_c"]

    def test_should_skip_ai_message_without_tool_calls(self):
        msg = Mock()
        msg.tool_calls = None
        result = extract_tool_calls([msg])
        assert result == []

    def test_should_skip_ai_message_with_empty_tool_calls(self):
        msg = Mock()
        msg.tool_calls = []
        result = extract_tool_calls([msg])
        assert result == []

    def test_should_skip_tool_call_without_name(self):
        tc = Mock()
        tc.name = ""
        msg = Mock()
        msg.tool_calls = [tc]
        result = extract_tool_calls([msg])
        assert result == []

    def test_should_skip_tool_message_without_name(self):
        msg = ToolMessage(content="ok", tool_call_id="tc1")
        result = extract_tool_calls([msg])
        assert result == []

    def test_should_skip_plain_messages(self):
        msg = HumanMessage(content="hello")
        result = extract_tool_calls([msg])
        assert result == []


# =============================================================================
# 2. enable_tool_error_handling 测试
# =============================================================================


class TestEnableToolErrorHandling:
    """测试启用工具错误容错"""

    def test_should_set_handle_tool_errors_to_true(self):
        tool_node = Mock()
        tool_node._handle_tool_errors = False
        tools_node = Mock()
        tools_node.bound = tool_node
        agent = Mock()
        agent.nodes = {"tools": tools_node}

        enable_tool_error_handling(agent)

        assert tool_node._handle_tool_errors is True

    def test_should_not_raise_when_tools_node_missing(self):
        agent = Mock()
        agent.nodes = {}
        enable_tool_error_handling(agent)

    def test_should_not_raise_when_tools_node_has_no_bound(self):
        tools_node = Mock(spec=[])
        agent = Mock()
        agent.nodes = {"tools": tools_node}
        enable_tool_error_handling(agent)

    def test_should_not_raise_when_agent_has_no_nodes(self):
        agent = Mock(spec=[])
        enable_tool_error_handling(agent)

    def test_should_not_raise_for_none_agent(self):
        enable_tool_error_handling(None)
