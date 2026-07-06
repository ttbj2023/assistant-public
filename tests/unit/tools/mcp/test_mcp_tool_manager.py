"""McpBridge单元测试.

测试 src/tools/mcp/mcp_tool_manager.py 的功能:
- McpServerConfig配置解析和验证
- 环境变量替换逻辑
- build_connection连接配置构建
- _resolve_project_name工具名映射
- _create_transport Transport创建
- _convert_tool MCP工具转换
- 懒加载和缓存逻辑
- 健康检查
- 重新加载
- 辅助函数(_schema_to_pydantic, _extract_call_result_text)

Mock边界:
- Mock fastmcp模块(sys.modules注入, 支持local import)
- Mock Client实例(MCP协议调用)
- 保留真实的配置解析和工具映射逻辑
"""

from __future__ import annotations

import os
import types
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.tools_config import McpServerConfig
from src.tools.mcp.mcp_tool_manager import McpBridge


def _make_mock_mcp_tool(
    name: str,
    description: str = "",
    input_schema: dict[str, Any] | None = None,
) -> MagicMock:
    """创建模拟的MCP Tool(模拟fastmcp list_tools()返回的工具对象)"""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = input_schema or {
        "type": "object",
        "properties": {},
    }
    return tool


def _make_mock_client(mcp_tools: list[MagicMock]) -> AsyncMock:
    """创建模拟的fastmcp Client实例.

    模拟Client的完整生命周期:
    - __aenter__() 返回自身(上下文管理器)
    - __aexit__() 静默关闭
    - list_tools() 返回给定的MCP工具列表
    """
    mock_client = AsyncMock(name="MockClientInstance")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.list_tools = AsyncMock(return_value=mcp_tools)
    return mock_client


@contextmanager
def _mock_fastmcp():
    """模拟fastmcp模块, 使_ensure_loaded中的local import成功.

    通过注入sys.modules, 使 from fastmcp.client import Client 等
    local import在fastmcp未安装的测试环境中也能工作.

    Yields:
        (mock_client_cls, mock_transports_mod) 元组
    """
    mock_client_cls = MagicMock(name="ClientClass")

    mock_client_mod = types.ModuleType("fastmcp.client")
    mock_client_mod.Client = mock_client_cls

    mock_transports_mod = types.ModuleType("fastmcp.client.transports")
    mock_transports_mod.StreamableHttpTransport = MagicMock(
        name="StreamableHttpTransport",
    )
    mock_transports_mod.SSETransport = MagicMock(name="SSETransport")
    mock_transports_mod.StdioTransport = MagicMock(name="StdioTransport")

    mock_fastmcp = types.ModuleType("fastmcp")

    modules = {
        "fastmcp": mock_fastmcp,
        "fastmcp.client": mock_client_mod,
        "fastmcp.client.transports": mock_transports_mod,
    }

    with patch.dict("sys.modules", modules):
        yield mock_client_cls, mock_transports_mod


# ============================================================
# McpServerConfig配置测试
# ============================================================


class TestMcpServerConfig:
    """MCP服务器配置模型测试"""

    def test_should_validate_name_not_empty(self):
        with pytest.raises(Exception):
            McpServerConfig(name="", transport="streamable_http")

    def test_should_use_defaults(self):
        config = McpServerConfig(
            name="minimal",
            transport="streamable_http",
            url="https://example.com/mcp",
        )
        assert config.enabled is True
        assert config.timeout > 0
        assert config.tool_names == {}
        assert config.response_formatters == {}
        assert config.tool_descriptions == {}
        assert config.local_args == {}
        assert config.max_concurrency == 0

    def test_should_reject_negative_max_concurrency(self):
        with pytest.raises(Exception):
            McpServerConfig(
                name="bad",
                transport="stdio",
                command="python",
                max_concurrency=-1,
            )


class TestEnvVarResolution:
    """环境变量替换测试"""

    def test_should_resolve_env_var_in_headers(self):
        config = McpServerConfig(
            name="test",
            transport="streamable_http",
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer ${TEST_API_KEY}"},
        )
        with patch.dict(os.environ, {"TEST_API_KEY": "my-secret-key"}):
            resolved = config.resolve_headers()
            assert resolved == {"Authorization": "Bearer my-secret-key"}

    def test_should_return_none_when_no_headers(self):
        config = McpServerConfig(
            name="test",
            transport="streamable_http",
            url="https://example.com/mcp",
        )
        assert config.resolve_headers() is None

    def test_should_handle_missing_env_var(self):
        config = McpServerConfig(
            name="test",
            transport="streamable_http",
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer ${NONEXISTENT_KEY}"},
        )
        with patch.dict(os.environ, {}, clear=True):
            resolved = config.resolve_headers()
            assert resolved == {"Authorization": "Bearer "}

    def test_should_resolve_multiple_vars(self):
        config = McpServerConfig(
            name="test",
            transport="streamable_http",
            url="https://example.com/mcp",
            headers={
                "Authorization": "Bearer ${API_KEY}",
                "X-Custom": "${CUSTOM_HEADER}",
            },
        )
        with patch.dict(
            os.environ,
            {"API_KEY": "key123", "CUSTOM_HEADER": "val456"},
        ):
            resolved = config.resolve_headers()
            assert resolved["Authorization"] == "Bearer key123"
            assert resolved["X-Custom"] == "val456"


class TestBuildConnection:
    """连接配置构建测试"""

    def test_should_build_streamable_http_connection(self):
        config = McpServerConfig(
            name="test",
            transport="streamable_http",
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer key"},
        )
        with patch.dict(os.environ, {"API_KEY": "key"}):
            conn = config.build_connection()

        assert conn["transport"] == "streamable_http"
        assert conn["url"] == "https://example.com/mcp"
        assert "Authorization" in conn["headers"]

    def test_should_build_stdio_connection(self):
        config = McpServerConfig(
            name="test",
            transport="stdio",
            command="python",
            args=["server.py"],
            env={"FOO": "bar"},
        )
        conn = config.build_connection()

        assert conn["transport"] == "stdio"
        assert conn["command"] == "python"
        assert conn["args"] == ["server.py"]
        assert conn["env"] == {"FOO": "bar"}

    def test_should_raise_on_missing_url_for_http(self):
        config = McpServerConfig(
            name="test",
            transport="streamable_http",
        )
        with pytest.raises(ValueError, match="streamable_http"):
            config.build_connection()

    def test_should_raise_on_missing_command_for_stdio(self):
        config = McpServerConfig(
            name="test",
            transport="stdio",
        )
        with pytest.raises(ValueError, match="stdio"):
            config.build_connection()

    def test_should_build_sse_connection(self):
        config = McpServerConfig(
            name="test",
            transport="sse",
            url="https://example.com/sse",
        )
        conn = config.build_connection()
        assert conn["transport"] == "sse"
        assert conn["url"] == "https://example.com/sse"

    def test_should_build_websocket_connection(self):
        config = McpServerConfig(
            name="test",
            transport="websocket",
            url="ws://example.com/ws",
        )
        conn = config.build_connection()
        assert conn["transport"] == "websocket"
        assert conn["url"] == "ws://example.com/ws"


# ============================================================
# McpBridge核心测试
# ============================================================


class TestMcpBridgeInit:
    """McpBridge初始化测试"""

    def test_should_create_server_semaphore_when_configured(self):
        configs = {
            "baidu_maps": McpServerConfig(
                name="baidu_maps",
                transport="stdio",
                command="npx",
                max_concurrency=2,
            ),
        }
        bridge = McpBridge(configs)
        assert "baidu_maps" in bridge._server_semaphores
        assert bridge._server_semaphores["baidu_maps"]._value == 2

    def test_should_not_create_semaphore_when_zero(self):
        configs = {
            "srv": McpServerConfig(
                name="srv",
                transport="streamable_http",
                url="https://example.com/mcp",
                max_concurrency=0,
            ),
        }
        bridge = McpBridge(configs)
        assert "srv" not in bridge._server_semaphores


class TestResolveProjectName:
    """工具名映射测试"""

    def test_should_map_to_project_name(self):
        bridge = McpBridge({})
        config = McpServerConfig(
            name="srv",
            transport="streamable_http",
            url="https://example.com/mcp",
            tool_names={"raw_search": "web_search"},
        )
        assert bridge._resolve_project_name("raw_search", config) == "web_search"

    def test_should_keep_original_name_when_no_mapping(self):
        bridge = McpBridge({})
        config = McpServerConfig(
            name="srv",
            transport="streamable_http",
            url="https://example.com/mcp",
        )
        assert bridge._resolve_project_name("original_name", config) == "original_name"


class TestCreateTransport:
    """Transport创建测试"""

    def test_should_create_streamable_http_transport(self):
        with _mock_fastmcp() as (_, mock_transports):
            bridge = McpBridge({})
            config = McpServerConfig(
                name="srv",
                transport="streamable_http",
                url="https://example.com/mcp",
                headers={"Authorization": "Bearer key"},
            )

            bridge._create_transport(config)

            mock_transports.StreamableHttpTransport.assert_called_once()
            call_kwargs = mock_transports.StreamableHttpTransport.call_args.kwargs
            assert call_kwargs["url"] == "https://example.com/mcp"

    def test_should_create_stdio_transport(self):
        with _mock_fastmcp() as (_, mock_transports):
            bridge = McpBridge({})
            config = McpServerConfig(
                name="srv",
                transport="stdio",
                command="python",
                args=["server.py"],
                env={"FOO": "bar"},
            )

            bridge._create_transport(config)

            mock_transports.StdioTransport.assert_called_once()
            call_kwargs = mock_transports.StdioTransport.call_args.kwargs
            assert call_kwargs["command"] == "python"
            assert call_kwargs["args"] == ["server.py"]

    def test_should_create_sse_transport(self):
        with _mock_fastmcp() as (_, mock_transports):
            bridge = McpBridge({})
            config = McpServerConfig(
                name="srv",
                transport="sse",
                url="https://example.com/sse",
            )

            bridge._create_transport(config)

            mock_transports.SSETransport.assert_called_once()

    def test_should_return_none_for_missing_url_on_http(self):
        with _mock_fastmcp():
            bridge = McpBridge({})
            config = McpServerConfig(
                name="srv",
                transport="streamable_http",
                url=None,
            )
            assert bridge._create_transport(config) is None

    def test_should_return_none_for_missing_url_on_sse(self):
        with _mock_fastmcp():
            bridge = McpBridge({})
            config = McpServerConfig(
                name="srv",
                transport="sse",
                url=None,
            )
            assert bridge._create_transport(config) is None

    def test_should_return_none_for_missing_command_on_stdio(self):
        with _mock_fastmcp():
            bridge = McpBridge({})
            config = McpServerConfig(
                name="srv",
                transport="stdio",
                command=None,
            )
            assert bridge._create_transport(config) is None

    def test_should_return_none_for_unknown_transport(self):
        with _mock_fastmcp():
            bridge = McpBridge({})
            config = McpServerConfig(
                name="srv",
                transport="streamable_http",
                url="https://example.com/mcp",
            )
            config.transport = "unknown_type"
            assert bridge._create_transport(config) is None

    def test_should_resolve_env_vars_in_stdio_env(self):
        with _mock_fastmcp() as (_, mock_transports):
            bridge = McpBridge({})
            config = McpServerConfig(
                name="srv",
                transport="stdio",
                command="uvx",
                args=["tool"],
                env={"API_KEY": "${MY_SECRET}"},
            )

            with patch.dict(os.environ, {"MY_SECRET": "resolved_val"}):
                bridge._create_transport(config)

            call_kwargs = mock_transports.StdioTransport.call_args.kwargs
            assert call_kwargs["env"] == {"API_KEY": "resolved_val"}


class TestConvertTool:
    """MCP工具转换测试"""

    def test_should_convert_to_structured_tool(self):
        with _mock_fastmcp():
            bridge = McpBridge({})
            mcp_tool = _make_mock_mcp_tool("web_search", "Search the web")

            tool = bridge._convert_tool(
                client=MagicMock(),
                mcp_tool=mcp_tool,
                project_name="datapro_search",
                formatter_name=None,
                server_name="zhipu",
                server_config=McpServerConfig(
                    name="zhipu",
                    transport="streamable_http",
                    url="https://example.com/mcp",
                ),
            )

            assert tool.name == "datapro_search"
            assert tool.description == "Search the web"
            assert tool.response_format == "content"
            assert tool.coroutine is not None

    def test_should_use_default_description_when_missing(self):
        with _mock_fastmcp():
            bridge = McpBridge({})
            mcp_tool = _make_mock_mcp_tool("tool_a")
            mcp_tool.description = ""

            tool = bridge._convert_tool(
                client=MagicMock(),
                mcp_tool=mcp_tool,
                project_name="tool_a",
                formatter_name=None,
                server_name="srv",
                server_config=McpServerConfig(
                    name="srv",
                    transport="streamable_http",
                    url="https://example.com/mcp",
                ),
            )

            assert "tool_a" in tool.description

    def test_should_create_tool_with_input_schema(self):
        with _mock_fastmcp():
            bridge = McpBridge({})
            mcp_tool = _make_mock_mcp_tool(
                "search",
                "Search",
                {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query",
                        },
                    },
                    "required": ["query"],
                },
            )

            tool = bridge._convert_tool(
                client=MagicMock(),
                mcp_tool=mcp_tool,
                project_name="search",
                formatter_name=None,
                server_name="srv",
                server_config=McpServerConfig(
                    name="srv",
                    transport="streamable_http",
                    url="https://example.com/mcp",
                ),
            )

            assert tool.args_schema is not None
            schema = tool.args_schema.model_json_schema()
            assert "query" in schema.get("properties", {})

    def test_should_merge_local_args_and_override_description(self):
        with _mock_fastmcp():
            bridge = McpBridge({})
            mcp_tool = _make_mock_mcp_tool(
                "dataPro_search",
                "Remote description",
                {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query",
                        },
                    },
                    "required": ["query"],
                },
            )

            tool = bridge._convert_tool(
                client=MagicMock(),
                mcp_tool=mcp_tool,
                project_name="datapro_search",
                formatter_name=None,
                server_name="datapro",
                server_config=McpServerConfig(
                    name="datapro",
                    transport="streamable_http",
                    url="https://example.com/mcp",
                    tool_descriptions={"dataPro_search": "Project description"},
                    local_args={
                        "dataPro_search": {
                            "format": {
                                "type": "string",
                                "description": "Output format",
                                "enum": ["compact", "full"],
                                "default": "compact",
                            },
                        },
                    },
                ),
            )

            schema = tool.args_schema.model_json_schema()
            assert tool.description == "Project description"
            assert "query" in schema["properties"]
            assert schema["properties"]["format"]["default"] == "compact"
            assert schema["properties"]["format"]["enum"] == ["compact", "full"]


class TestMcpCallWithRetry:
    """MCP调用参数处理测试"""

    @pytest.mark.asyncio
    async def test_should_strip_local_args_before_remote_call(self):
        from src.tools.mcp.mcp_tool_manager import _call_with_retry

        client = AsyncMock()
        client.call_tool = AsyncMock(return_value="raw result")
        formatter = MagicMock()
        formatter.safe_format.return_value = "formatted result"
        server_config = McpServerConfig(
            name="datapro",
            transport="streamable_http",
            url="https://example.com/mcp",
            local_args={
                "dataPro_search": {
                    "format": {
                        "type": "string",
                        "default": "compact",
                    },
                },
            },
        )
        bridge = McpBridge({"datapro": server_config})

        result = await _call_with_retry(
            client=client,
            tool_name="dataPro_search",
            kwargs={"query": "比亚迪 ROE", "format": "full", "unused": None},
            formatter=formatter,
            server_name="datapro",
            server_config=server_config,
            bridge=bridge,
        )

        assert result == "formatted result"
        client.call_tool.assert_awaited_once_with(
            "dataPro_search",
            {"query": "比亚迪 ROE"},
        )
        formatter.safe_format.assert_called_once_with(
            "raw result",
            query="比亚迪 ROE",
            format="full",
            unused=None,
        )


class TestMcpBridgeLazyLoad:
    """懒加载测试"""

    @pytest.mark.asyncio
    async def test_should_load_tools_on_first_access(self):
        mcp_tool = _make_mock_mcp_tool("datapro_search", "Search")

        configs = {
            "zhipu": McpServerConfig(
                name="zhipu",
                transport="streamable_http",
                url="https://example.com/mcp",
                tool_names={"datapro_search": "web_search"},
            ),
        }
        bridge = McpBridge(configs)

        with _mock_fastmcp() as (mock_client_cls, _):
            mock_client = _make_mock_client([mcp_tool])
            mock_client_cls.return_value = mock_client

            with patch.object(bridge, "_create_transport", return_value=MagicMock()):
                tool = await bridge.get_tool("web_search")

            assert tool is not None
            assert tool.name == "web_search"
            assert bridge._loaded is True
            mock_client.list_tools.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_cache_tools_after_first_load(self):
        mcp_tool = _make_mock_mcp_tool("tool_a", "Tool A")

        bridge = McpBridge({
            "srv": McpServerConfig(
                name="srv",
                transport="streamable_http",
                url="https://example.com/mcp",
            ),
        })

        with _mock_fastmcp() as (mock_client_cls, _):
            mock_client = _make_mock_client([mcp_tool])
            mock_client_cls.return_value = mock_client

            with patch.object(bridge, "_create_transport", return_value=MagicMock()):
                await bridge.get_tool("tool_a")
                await bridge.get_tool("tool_a")

            mock_client.list_tools.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_return_none_for_unknown_tool(self):
        bridge = McpBridge({})

        with _mock_fastmcp() as (mock_client_cls, _):
            mock_client = _make_mock_client([])
            mock_client_cls.return_value = mock_client

            tool = await bridge.get_tool("nonexistent")

            assert tool is None

    @pytest.mark.asyncio
    async def test_should_handle_load_failure_gracefully(self):
        bridge = McpBridge({
            "srv": McpServerConfig(
                name="srv",
                transport="streamable_http",
                url="https://example.com/mcp",
            ),
        })

        with _mock_fastmcp() as (mock_client_cls, _):
            mock_client = _make_mock_client([])
            mock_client.__aenter__ = AsyncMock(
                side_effect=Exception("Connection failed"),
            )
            mock_client_cls.return_value = mock_client

            with patch.object(bridge, "_create_transport", return_value=MagicMock()):
                tool = await bridge.get_tool("any_tool")

            assert tool is None
            assert bridge._loaded is True

    @pytest.mark.asyncio
    async def test_should_continue_loading_after_server_failure(self):
        """单个服务器加载失败不应阻塞其他服务器"""
        mcp_tool = _make_mock_mcp_tool("working_tool", "Works")

        configs = {
            "bad_srv": McpServerConfig(
                name="bad_srv",
                transport="streamable_http",
                url="https://bad.example.com/mcp",
            ),
            "good_srv": McpServerConfig(
                name="good_srv",
                transport="streamable_http",
                url="https://good.example.com/mcp",
            ),
        }
        bridge = McpBridge(configs)

        call_count = 0

        def create_client(*args: Any, **kwargs: Any) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                mock_bad = AsyncMock()
                mock_bad.__aenter__ = AsyncMock(
                    side_effect=Exception("Connection refused"),
                )
                mock_bad.__aexit__ = AsyncMock(return_value=None)
                return mock_bad
            return _make_mock_client([mcp_tool])

        with _mock_fastmcp() as (mock_client_cls, _):
            mock_client_cls.side_effect = create_client

            with patch.object(bridge, "_create_transport", return_value=MagicMock()):
                await bridge.get_all_tools()

            assert bridge._loaded is True
            assert "working_tool" in bridge._tools

    @pytest.mark.asyncio
    async def test_should_skip_disabled_servers(self):
        mcp_tool = _make_mock_mcp_tool("tool_a", "Tool")

        configs = {
            "disabled": McpServerConfig(
                name="disabled",
                transport="streamable_http",
                url="https://example.com/mcp",
                enabled=False,
            ),
            "enabled": McpServerConfig(
                name="enabled",
                transport="streamable_http",
                url="https://example.com/mcp",
            ),
        }
        bridge = McpBridge(configs)

        with _mock_fastmcp() as (mock_client_cls, _):
            mock_client = _make_mock_client([mcp_tool])
            mock_client_cls.return_value = mock_client

            with patch.object(bridge, "_create_transport", return_value=MagicMock()):
                await bridge.get_all_tools()

            mock_client_cls.assert_called_once()
            assert "tool_a" in bridge._tools


class TestMcpBridgeStats:
    """统计信息测试"""

    def test_should_include_concurrency_limits_in_stats(self):
        configs = {
            "baidu_maps": McpServerConfig(
                name="baidu_maps",
                transport="stdio",
                command="npx",
                max_concurrency=2,
            ),
            "other": McpServerConfig(
                name="other",
                transport="streamable_http",
                url="https://example.com/mcp",
            ),
        }
        bridge = McpBridge(configs)
        stats = bridge.get_stats()
        assert stats["concurrency_limits"] == {"baidu_maps": 2}
        assert "other" not in stats["concurrency_limits"]

    @pytest.mark.asyncio
    async def test_should_return_stats_after_load(self):
        configs = {
            "srv": McpServerConfig(
                name="srv",
                transport="streamable_http",
                url="https://example.com/mcp",
                tool_names={"raw": "project_name"},
            ),
        }
        bridge = McpBridge(configs)

        with _mock_fastmcp() as (mock_client_cls, _):
            mock_client = _make_mock_client(
                [_make_mock_mcp_tool("raw", "d")],
            )
            mock_client_cls.return_value = mock_client

            with patch.object(bridge, "_create_transport", return_value=MagicMock()):
                await bridge.get_all_tools()

        stats = bridge.get_stats()
        assert stats["total_tools"] == 1
        assert stats["loaded"] is True
        assert stats["servers"] == 1
        assert stats["active_clients"] == 1
        assert "project_name" in stats["tool_names"]


class TestMcpBridgeHealthCheck:
    """健康检查测试"""

    @pytest.mark.asyncio
    async def test_should_report_pending_before_load(self):
        configs = {
            "srv": McpServerConfig(
                name="srv",
                transport="streamable_http",
                url="https://example.com/mcp",
            ),
        }
        bridge = McpBridge(configs)

        health = await bridge.health_check()

        assert health["healthy"] is True
        assert "srv" in health["servers"]
        assert health["servers"]["srv"]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_should_report_configured_after_load(self):
        configs = {
            "srv": McpServerConfig(
                name="srv",
                transport="streamable_http",
                url="https://example.com/mcp",
                tool_names={"raw": "mapped_tool"},
            ),
        }
        bridge = McpBridge(configs)

        with _mock_fastmcp() as (mock_client_cls, _):
            mock_client = _make_mock_client(
                [_make_mock_mcp_tool("raw", "d")],
            )
            mock_client_cls.return_value = mock_client

            with patch.object(bridge, "_create_transport", return_value=MagicMock()):
                await bridge.get_all_tools()

        health = await bridge.health_check()

        assert health["servers"]["srv"]["status"] == "configured"
        assert health["servers"]["srv"]["tool_count"] == 1

    @pytest.mark.asyncio
    async def test_should_report_disabled_servers(self):
        configs = {
            "disabled_srv": McpServerConfig(
                name="disabled_srv",
                transport="streamable_http",
                url="https://example.com/mcp",
                enabled=False,
            ),
        }
        bridge = McpBridge(configs)

        health = await bridge.health_check()

        assert health["servers"]["disabled_srv"]["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_should_report_loaded_tools_count(self):
        configs = {
            "srv": McpServerConfig(
                name="srv",
                transport="streamable_http",
                url="https://example.com/mcp",
                tool_names={"a": "tool_a", "b": "tool_b"},
            ),
        }
        bridge = McpBridge(configs)

        with _mock_fastmcp() as (mock_client_cls, _):
            mock_client = _make_mock_client([
                _make_mock_mcp_tool("a", "A"),
                _make_mock_mcp_tool("b", "B"),
            ])
            mock_client_cls.return_value = mock_client

            with patch.object(bridge, "_create_transport", return_value=MagicMock()):
                await bridge.get_all_tools()

        health = await bridge.health_check()
        assert health["loaded_tools"] == 2


class TestMcpBridgeReload:
    """重新加载测试"""

    @pytest.mark.asyncio
    async def test_should_reload_tools(self):
        bridge = McpBridge({
            "srv": McpServerConfig(
                name="srv",
                transport="streamable_http",
                url="https://example.com/mcp",
            ),
        })

        with _mock_fastmcp() as (mock_client_cls, _):
            mock_client = _make_mock_client(
                [_make_mock_mcp_tool("tool_v1", "v1")],
            )
            mock_client_cls.return_value = mock_client

            with patch.object(bridge, "_create_transport", return_value=MagicMock()):
                await bridge.get_all_tools()
                assert len(bridge._tools) == 1

                mock_client.list_tools = AsyncMock(
                    return_value=[
                        _make_mock_mcp_tool("tool_v1", "v1"),
                        _make_mock_mcp_tool("tool_v2", "v2"),
                    ],
                )

                await bridge.reload()
                assert len(bridge._tools) == 2

    @pytest.mark.asyncio
    async def test_should_close_clients_on_reload(self):
        bridge = McpBridge({
            "srv": McpServerConfig(
                name="srv",
                transport="streamable_http",
                url="https://example.com/mcp",
            ),
        })

        with _mock_fastmcp() as (mock_client_cls, _):
            mock_client = _make_mock_client(
                [_make_mock_mcp_tool("tool_a", "A")],
            )
            mock_client_cls.return_value = mock_client

            with patch.object(bridge, "_create_transport", return_value=MagicMock()):
                await bridge.get_all_tools()

                assert len(bridge._clients) == 1

                mock_client.list_tools = AsyncMock(return_value=[])
                await bridge.reload()

                # reload内部先close(清空_clients), 再重新加载
                # 第二次加载返回空工具列表, clients中有一个新client
                assert len(bridge._clients) == 1
                mock_client.__aexit__.assert_called()


class TestMcpBridgeClose:
    """连接关闭测试"""

    @pytest.mark.asyncio
    async def test_should_close_all_clients(self):
        bridge = McpBridge({})

        mock_client1 = AsyncMock()
        mock_client1.__aexit__ = AsyncMock(return_value=None)
        mock_client2 = AsyncMock()
        mock_client2.__aexit__ = AsyncMock(return_value=None)

        bridge._clients = [mock_client1, mock_client2]

        await bridge.close()

        mock_client1.__aexit__.assert_called_once()
        mock_client2.__aexit__.assert_called_once()
        assert len(bridge._clients) == 0

    @pytest.mark.asyncio
    async def test_should_handle_close_error_gracefully(self):
        bridge = McpBridge({})

        mock_client = AsyncMock()
        mock_client.__aexit__ = AsyncMock(side_effect=RuntimeError("close error"))

        bridge._clients = [mock_client]

        await bridge.close()

        assert len(bridge._clients) == 0


class TestSchemaToPydantic:
    """_schema_to_pydantic辅助函数测试"""

    def test_should_create_model_from_simple_schema(self):
        from src.tools.mcp.mcp_tool_manager import _schema_to_pydantic

        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results"},
            },
            "required": ["query"],
        }

        model = _schema_to_pydantic(schema, "TestInput")
        fields = model.model_fields

        assert "query" in fields
        assert "limit" in fields

    def test_should_handle_empty_schema(self):
        from src.tools.mcp.mcp_tool_manager import _schema_to_pydantic

        model = _schema_to_pydantic(
            {"type": "object", "properties": {}},
            "EmptyInput",
        )
        assert len(model.model_fields) == 0

    def test_should_handle_required_and_optional_fields(self):
        from src.tools.mcp.mcp_tool_manager import _schema_to_pydantic

        schema = {
            "type": "object",
            "properties": {
                "required_field": {"type": "string"},
                "optional_field": {"type": "boolean"},
            },
            "required": ["required_field"],
        }

        model = _schema_to_pydantic(schema, "MixedInput")
        instance = model(required_field="test")
        assert instance.required_field == "test"
        assert instance.optional_field is None

    def test_should_support_all_basic_types(self):
        from src.tools.mcp.mcp_tool_manager import _schema_to_pydantic

        schema = {
            "type": "object",
            "properties": {
                "s": {"type": "string"},
                "i": {"type": "integer"},
                "f": {"type": "number"},
                "b": {"type": "boolean"},
            },
            "required": ["s", "i", "f", "b"],
        }

        model = _schema_to_pydantic(schema, "AllTypesInput")
        instance = model(s="hello", i=42, f=3.14, b=True)
        assert instance.s == "hello"
        assert instance.i == 42
        assert instance.f == 3.14
        assert instance.b is True

    def test_should_default_to_string_for_unknown_type(self):
        from src.tools.mcp.mcp_tool_manager import _schema_to_pydantic

        schema = {
            "type": "object",
            "properties": {
                "unknown": {"type": "nonexistent_type"},
            },
            "required": ["unknown"],
        }

        model = _schema_to_pydantic(schema, "UnknownTypeInput")
        instance = model(unknown="not_an_array")
        assert instance.unknown == "not_an_array"


class TestExtractCallResultText:
    """_extract_call_result_text辅助函数测试"""

    def test_should_extract_from_string(self):
        from src.tools.mcp.mcp_tool_manager import _extract_call_result_text

        assert _extract_call_result_text("hello") == "hello"

    def test_should_extract_from_content_list_with_text_attr(self):
        from src.tools.mcp.mcp_tool_manager import _extract_call_result_text

        mock_result = MagicMock()
        item1 = MagicMock()
        item1.text = "line1"
        item2 = MagicMock()
        item2.text = "line2"
        mock_result.content = [item1, item2]

        result = _extract_call_result_text(mock_result)
        assert "line1" in result
        assert "line2" in result

    def test_should_extract_from_dict_list(self):
        from src.tools.mcp.mcp_tool_manager import _extract_call_result_text

        mock_result = MagicMock()
        mock_result.content = [{"text": "part1"}, {"text": "part2"}]

        result = _extract_call_result_text(mock_result)
        assert "part1" in result
        assert "part2" in result

    def test_should_extract_from_string_list(self):
        from src.tools.mcp.mcp_tool_manager import _extract_call_result_text

        mock_result = MagicMock()
        mock_result.content = ["hello", "world"]

        result = _extract_call_result_text(mock_result)
        assert "hello" in result
        assert "world" in result

    def test_should_handle_empty_result(self):
        from src.tools.mcp.mcp_tool_manager import _extract_call_result_text

        assert _extract_call_result_text(None) == ""
        assert _extract_call_result_text("") == ""

    def test_should_handle_raw_list_without_content_attr(self):
        from src.tools.mcp.mcp_tool_manager import _extract_call_result_text

        items = [MagicMock(text="a"), {"text": "b"}]
        result = _extract_call_result_text(items)
        assert "a" in result
        assert "b" in result
