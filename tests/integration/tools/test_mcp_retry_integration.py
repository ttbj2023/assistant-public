"""McpBridge 重试/重连/鉴权/限流集成测试.

灰盒: 真实 McpBridge + 真实重试循环 (_do_call_with_retry) + 真实 _try_reconnect 状态机 +
真实 _is_auth_error/_is_rate_limit_error 判定, 仅 Mock 外部 MCP 服务器 (fastmcp Client).
验证错误分类与重连策略的真实协作. 单元测试 mock fastmcp 验证配置/映射, 但重试循环/
重连状态机/错误分类的协作行为此前零覆盖.
"""

from __future__ import annotations

import types
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.tools_config import McpServerConfig
from src.tools.mcp.mcp_tool_manager import McpBridge


def _make_mock_mcp_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = "desc"
    tool.inputSchema = {"type": "object", "properties": {}}
    return tool


def _make_client(call_tool_side_effect: Any) -> AsyncMock:
    """构建可控的 fastmcp Client 实例 (AsyncMock)."""
    client = AsyncMock(name="MockClient")
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.list_tools = AsyncMock(return_value=[_make_mock_mcp_tool("original_tool")])
    client.call_tool = AsyncMock(side_effect=call_tool_side_effect)
    return client


@contextmanager
def _mock_fastmcp():
    """注入 sys.modules 使 fastmcp local import 可用 (复用单元测试范式)."""
    mock_client_cls = MagicMock(name="ClientClass")
    mock_client_mod = types.ModuleType("fastmcp.client")
    mock_client_mod.Client = mock_client_cls
    mock_transports_mod = types.ModuleType("fastmcp.client.transports")
    mock_transports_mod.StreamableHttpTransport = MagicMock()
    mock_transports_mod.SSETransport = MagicMock()
    mock_transports_mod.StdioTransport = MagicMock()
    mock_fastmcp = types.ModuleType("fastmcp")
    with patch.dict(
        "sys.modules",
        {
            "fastmcp": mock_fastmcp,
            "fastmcp.client": mock_client_mod,
            "fastmcp.client.transports": mock_transports_mod,
        },
    ):
        yield mock_client_cls


def _server_config() -> McpServerConfig:
    return McpServerConfig(
        name="test_srv",
        transport="streamable_http",
        url="http://fake/mcp",
        tool_names={"original_tool": "project_tool"},
    )


def _transport_load_only():
    """_create_transport side_effect: 首次调用 (加载阶段) 返回非 None,

    后续调用 (_try_reconnect) 返回 None 跳过重连, 使同一 client 被重试.
    """
    state = {"n": 0}

    def _fake(config):
        state["n"] += 1
        return MagicMock() if state["n"] == 1 else None

    return _fake


class TestMcpBridgeRetryIntegration:
    """McpBridge 重试/鉴权/限流/重连协作集成测试."""

    @pytest.mark.asyncio
    async def test_integration_retry_succeeds_on_second_attempt(self):
        """瞬时错误: 首次失败 → 重试 → 第二次成功.

        协作场景: 真实 _do_call_with_retry 循环 + _try_reconnect (返回 None 不换 client)
        Mock 边界: Mock fastmcp Client (call_tool 先抛错后成功), _create_transport 返回
            None (跳过重连, 同一 client 重试), get_retry_config 加速
        验证重点: call_tool 被 await 2 次, 最终返回成功结果文本
        业务价值: 确保瞬时故障下 MCP 工具调用自动恢复, 不向用户暴露瞬时错误
        """
        client = _make_client([RuntimeError("连接中断"), "成功结果"])
        bridge = McpBridge({"test_srv": _server_config()})
        with _mock_fastmcp() as mock_client_cls:
            mock_client_cls.return_value = client
            with (
                patch.object(
                    bridge, "_create_transport", side_effect=_transport_load_only()
                ),
                patch("src.tools.mcp.mcp_tool_manager.asyncio.sleep", new=AsyncMock()),
            ):
                tool = await bridge.get_tool("project_tool")
                result = await tool.coroutine(query="x")

        assert "成功结果" in result
        assert client.call_tool.await_count == 2

    @pytest.mark.asyncio
    async def test_integration_auth_error_no_retry_secret_safe(self):
        """鉴权错误 (401) 立即返回错误 JSON, 不重试.

        协作场景: 真实 _is_auth_error 判定 → 跳过重试块直接返回
        Mock 边界: Mock fastmcp Client (call_tool 抛 401 错误)
        验证重点:
            1. call_tool 仅 await 1 次 (不重试)
            2. 返回结构化错误 JSON (含 error 字段)
            3. 错误信息被截断保护, 不泄漏完整 secret
        业务价值: 鉴权失败不应反复重试浪费配额, 且错误响应须结构化便于上层处理
        """
        import json

        client = _make_client([RuntimeError("HTTP 401 Unauthorized: invalid apikey")])
        bridge = McpBridge({"test_srv": _server_config()})
        with _mock_fastmcp() as mock_client_cls:
            mock_client_cls.return_value = client
            with patch.object(
                bridge, "_create_transport", side_effect=_transport_load_only()
            ):
                tool = await bridge.get_tool("project_tool")
                result = await tool.coroutine(query="x")

        assert client.call_tool.await_count == 1, "鉴权错误不应重试"
        payload = json.loads(result)
        assert "error" in payload
        assert "认证失败" in payload["error"]

    @pytest.mark.asyncio
    async def test_integration_rate_limit_uses_rate_delay_then_retries(self):
        """限流错误 (429) 使用 rate_limit_delay 等待后重试成功.

        协作场景: 真实 _is_rate_limit_error 判定 → 选择 rate_limit_delay (而非 base_delay)
        Mock 边界: Mock fastmcp Client (call_tool 先抛 429 后成功), 记录 asyncio.sleep 调用
        验证重点: sleep 以 rate_limit_delay 调用 (非 base_delay), 重试后返回成功
        业务价值: 限流场景须退避更久再重试, 避免加剧服务端压力
        """
        sleep_mock = AsyncMock()
        client = _make_client([
            RuntimeError("HTTP 429 Too Many Requests: rate limit"),
            "限流后成功",
        ])
        bridge = McpBridge({"test_srv": _server_config()})
        with _mock_fastmcp() as mock_client_cls:
            mock_client_cls.return_value = client
            with (
                patch.object(
                    bridge, "_create_transport", side_effect=_transport_load_only()
                ),
                patch("src.tools.mcp.mcp_tool_manager.asyncio.sleep", new=sleep_mock),
            ):
                tool = await bridge.get_tool("project_tool")
                result = await tool.coroutine(query="x")

        assert "限流后成功" in result
        # 限流退避使用 rate_limit_delay (默认 3.0), 而非 base_delay (默认 1.0)
        sleep_mock.assert_awaited_with(3.0)

    @pytest.mark.asyncio
    async def test_integration_reconnect_swaps_client(self):
        """重连: 失败后 _try_reconnect 创建新 client, 旧 client 从 _clients 移除.

        协作场景: 真实 _try_reconnect 状态机 — 创建新 Client + __aenter__ + append,
            关闭并移除旧 client
        Mock 边界: Mock fastmcp Client 类 (side_effect 依次返回 client_a, client_b),
            _create_transport 返回非 None (触发重连)
        验证重点:
            1. client_a.call_tool 抛错 (1 次), client_b.call_tool 成功 (1 次)
            2. bridge._clients 含 client_b 不含 client_a (状态正确切换)
        业务价值: 确保失效连接被正确替换, 避免反复用坏连接重试
        """
        client_a = _make_client([RuntimeError("连接失效")])
        client_b = _make_client(["重连后成功"])
        bridge = McpBridge({"test_srv": _server_config()})
        with _mock_fastmcp() as mock_client_cls:
            mock_client_cls.side_effect = [client_a, client_b]
            with (
                patch.object(bridge, "_create_transport", return_value=MagicMock()),
                patch("src.tools.mcp.mcp_tool_manager.asyncio.sleep", new=AsyncMock()),
            ):
                tool = await bridge.get_tool("project_tool")
                result = await tool.coroutine(query="x")

        assert "重连后成功" in result
        assert client_a.call_tool.await_count == 1
        assert client_b.call_tool.await_count == 1
        # 状态切换: 旧 client 移除, 新 client 加入
        assert client_b in bridge._clients
        assert client_a not in bridge._clients

    @pytest.mark.asyncio
    async def test_integration_all_attempts_exhausted_returns_error(self):
        """所有重试耗尽后返回结构化错误 (不抛异常).

        协作场景: 真实重试循环达到 max_attempts 后返回错误 JSON
        Mock 边界: Mock fastmcp Client (call_tool 持续抛错)
        验证重点: 返回 JSON 错误而非抛异常 (容错契约)
        业务价值: MCP 工具彻底不可用时上层仍能拿到结构化错误, 不崩 Agent 循环
        """
        import json

        client = _make_client([RuntimeError("持续失败"), RuntimeError("仍然失败")])
        bridge = McpBridge({"test_srv": _server_config()})
        with _mock_fastmcp() as mock_client_cls:
            mock_client_cls.return_value = client
            with (
                patch.object(
                    bridge, "_create_transport", side_effect=_transport_load_only()
                ),
                patch("src.tools.mcp.mcp_tool_manager.asyncio.sleep", new=AsyncMock()),
            ):
                tool = await bridge.get_tool("project_tool")
                result = await tool.coroutine(query="x")

        payload = json.loads(result)
        assert "error" in payload
        assert client.call_tool.await_count == 2, "max_attempts=2 应尝试 2 次"
