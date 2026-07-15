"""Tools模块配置系统单元测试.

覆盖 tools_config.py 的核心逻辑:
- InternalToolConfig / McpServerConfig 验证
- McpServerConfig.resolve_headers (环境变量替换)
- McpServerConfig.build_connection (连接配置构建)
- ToolsConfig 查询方法
- 环境变量替换 (_resolve_env_vars)
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.config.tools_config import (
    InternalToolConfig,
    McpServerConfig,
    ToolGroupConfig,
    ToolsConfig,
)


class TestInternalToolConfig:
    """内部工具配置验证测试."""

    def test_valid_config(self) -> None:
        cfg = InternalToolConfig(
            name="create_todo",
            class_path="src.tools.internal.create_todo_tool.CreateTodoTool",
        )
        assert cfg.name == "create_todo"
        assert cfg.enabled is True

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            InternalToolConfig(name="  ", class_path="some.path")

    def test_empty_class_path_raises(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            InternalToolConfig(name="tool", class_path="")

    def test_whitespace_trimmed(self) -> None:
        cfg = InternalToolConfig(name="  tool  ", class_path="  path  ")
        assert cfg.name == "tool"
        assert cfg.class_path == "path"

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(Exception):
            InternalToolConfig(name="t", class_path="p", timeout=-1)


class TestToolGroupConfig:
    """工具组配置验证测试."""

    def test_valid_config(self) -> None:
        cfg = ToolGroupConfig(
            name="todo_manager_group",
            summary="TODO管理",
            members=["create_todo"],
        )
        assert cfg.name == "todo_manager_group"
        assert cfg.members == ["create_todo"]

    def test_name_must_end_with_group_suffix(self) -> None:
        """组名必须以 _group 结尾(命名约定, 保证 display_label 派生可靠)."""
        with pytest.raises(ValueError, match="_group"):
            ToolGroupConfig(name="todo_manager", summary="s", members=["m"])

    def test_name_cannot_be_empty(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            ToolGroupConfig(name="", summary="s", members=["m"])

    def test_display_label_strips_group_suffix(self) -> None:
        """display_label 为组名去 _group 后缀, 用于面向模型的文本."""
        cfg = ToolGroupConfig(
            name="scheduled_messenger_group", summary="s", members=["m"]
        )
        assert cfg.display_label == "scheduled_messenger"

    def test_display_label_of_short_group_name(self) -> None:
        """组名 'g_group' 的 display_label 为 'g'."""
        cfg = ToolGroupConfig(name="g_group", summary="s", members=["m"])
        assert cfg.display_label == "g"


class TestMcpServerConfig:
    """MCP服务器配置测试."""

    def test_valid_streamable_http(self) -> None:
        cfg = McpServerConfig(
            name="zhipu",
            transport="streamable_http",
            url="https://api.example.com/mcp",
            headers={"Authorization": "Bearer key"},
        )
        assert cfg.transport == "streamable_http"

    def test_valid_stdio(self) -> None:
        cfg = McpServerConfig(
            name="local",
            transport="stdio",
            command="uvx",
            args=["tool-name"],
        )
        assert cfg.command == "uvx"

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            McpServerConfig(name="  ", transport="stdio")

    def test_resolve_headers_no_env(self) -> None:
        cfg = McpServerConfig(
            name="test",
            transport="streamable_http",
            headers={"X-Custom": "value"},
        )
        resolved = cfg.resolve_headers()
        assert resolved == {"X-Custom": "value"}

    def test_resolve_headers_with_env_var(self) -> None:
        cfg = McpServerConfig(
            name="test",
            transport="streamable_http",
            headers={"Authorization": "Bearer ${TEST_API_KEY}"},
        )
        with patch.dict(os.environ, {"TEST_API_KEY": "sk-12345"}):
            resolved = cfg.resolve_headers()
            assert resolved["Authorization"] == "Bearer sk-12345"

    def test_resolve_headers_missing_env_var(self) -> None:
        cfg = McpServerConfig(
            name="test",
            transport="streamable_http",
            headers={"Authorization": "Bearer ${NONEXISTENT_KEY}"},
        )
        with patch.dict(os.environ, {}, clear=True):
            resolved = cfg.resolve_headers()
            assert resolved["Authorization"] == "Bearer "

    def test_resolve_headers_none(self) -> None:
        cfg = McpServerConfig(name="test", transport="stdio")
        assert cfg.resolve_headers() is None

    def test_build_connection_streamable_http(self) -> None:
        cfg = McpServerConfig(
            name="test",
            transport="streamable_http",
            url="https://api.example.com/mcp",
        )
        conn = cfg.build_connection()
        assert conn["transport"] == "streamable_http"
        assert conn["url"] == "https://api.example.com/mcp"

    def test_build_connection_streamable_http_no_url(self) -> None:
        cfg = McpServerConfig(name="test", transport="streamable_http", url=None)
        with pytest.raises(ValueError, match="需要url"):
            cfg.build_connection()

    def test_build_connection_stdio(self) -> None:
        cfg = McpServerConfig(
            name="test",
            transport="stdio",
            command="uvx",
            args=["tool"],
        )
        conn = cfg.build_connection()
        assert conn["command"] == "uvx"
        assert conn["args"] == ["tool"]

    def test_build_connection_stdio_no_command(self) -> None:
        cfg = McpServerConfig(name="test", transport="stdio", command=None)
        with pytest.raises(ValueError, match="需要command"):
            cfg.build_connection()

    def test_build_connection_sse(self) -> None:
        cfg = McpServerConfig(
            name="test",
            transport="sse",
            url="https://api.example.com/sse",
        )
        conn = cfg.build_connection()
        assert conn["url"] == "https://api.example.com/sse"

    def test_build_connection_websocket(self) -> None:
        cfg = McpServerConfig(
            name="test",
            transport="websocket",
            url="wss://api.example.com/ws",
        )
        conn = cfg.build_connection()
        assert conn["url"] == "wss://api.example.com/ws"

    def test_build_connection_websocket_no_url(self) -> None:
        cfg = McpServerConfig(name="test", transport="websocket", url=None)
        with pytest.raises(ValueError, match="需要url"):
            cfg.build_connection()

    def test_resolve_env_vars(self) -> None:
        with patch.dict(os.environ, {"MY_VAR": "hello"}):
            result = McpServerConfig._resolve_env_vars("prefix_${MY_VAR}_suffix")
            assert result == "prefix_hello_suffix"

    def test_resolve_env_vars_no_match(self) -> None:
        result = McpServerConfig._resolve_env_vars("no_vars_here")
        assert result == "no_vars_here"


class TestToolsConfig:
    """ToolsConfig 主配置类测试."""

    def test_get_internal_tool_config_exists(self) -> None:
        config = ToolsConfig(
            internal_tools={
                "todo": InternalToolConfig(name="todo", class_path="path.ToTool"),
            }
        )
        result = config.get_internal_tool_config("todo")
        assert result is not None
        assert result.name == "todo"

    def test_get_internal_tool_config_not_exists(self) -> None:
        config = ToolsConfig()
        assert config.get_internal_tool_config("nonexistent") is None

    def test_get_mcp_server_config_exists(self) -> None:
        config = ToolsConfig(
            mcp_servers={
                "test": McpServerConfig(name="test", transport="stdio", command="cmd"),
            }
        )
        result = config.get_mcp_server_config("test")
        assert result is not None

    def test_get_mcp_server_config_not_exists(self) -> None:
        config = ToolsConfig()
        assert config.get_mcp_server_config("nonexistent") is None

    def test_list_enabled_internal_tools(self) -> None:
        config = ToolsConfig(
            internal_tools={
                "a": InternalToolConfig(name="a", class_path="p1", enabled=True),
                "b": InternalToolConfig(name="b", class_path="p2", enabled=False),
                "c": InternalToolConfig(name="c", class_path="p3", enabled=True),
            }
        )
        enabled = config.list_enabled_internal_tools()
        assert len(enabled) == 2
        assert all(t.enabled for t in enabled)

    def test_list_enabled_mcp_servers(self) -> None:
        config = ToolsConfig(
            mcp_servers={
                "a": McpServerConfig(
                    name="a", transport="stdio", command="c1", enabled=True
                ),
                "b": McpServerConfig(
                    name="b", transport="stdio", command="c2", enabled=False
                ),
            }
        )
        enabled = config.list_enabled_mcp_servers()
        assert len(enabled) == 1

    def test_default_config_includes_python_executor_tool(self) -> None:
        default_config = ToolsConfig.get_default_config()
        tool_config = default_config["external_tools"]["python_executor"]
        assert "PythonExecutorTool" in tool_config["class_path"]
        # base_url 不再走 config, 由 TOOL_RUNTIME_BASE_URL 环境变量驱动(与 skill_executor 一致)
        assert "base_url" not in tool_config["config"]
        assert isinstance(
            tool_config["config"]["default_timeout_seconds"], (int, float)
        )


class TestToolGroupsMerge:
    """tool_groups 与其他配置类别一致, 采用 per-item 字段级合并(非整体替换)."""

    @pytest.fixture
    def _isolate_env(self):
        """tools 配置不再读取通用 env overlay."""
        yield

    def test_partial_yaml_keeps_default_prompt_hint(self, _isolate_env) -> None:
        """yaml 只覆盖部分字段时, 默认的 prompt_hint/members 保留(整体替换会导致丢失)."""
        yaml_cfg = {
            "tool_groups": {"todo_manager_group": {"keywords": ["自定义关键词"]}}
        }
        with patch(
            "src.config.tools_config.get_module_config_sync",
            return_value=yaml_cfg,
        ):
            config = ToolsConfig.from_module_config()
        group = config.tool_groups["todo_manager_group"]
        assert group.keywords == ["自定义关键词"]
        assert group.prompt_hint  # 默认 prompt_hint 保留(非空)
        assert "create_todo" in group.members  # 默认 members 保留

    def test_yaml_can_override_prompt_hint(self, _isolate_env) -> None:
        """yaml 可显式覆盖默认 prompt_hint."""
        yaml_cfg = {
            "tool_groups": {"todo_manager_group": {"prompt_hint": "自定义提示"}}
        }
        with patch(
            "src.config.tools_config.get_module_config_sync",
            return_value=yaml_cfg,
        ):
            config = ToolsConfig.from_module_config()
        assert config.tool_groups["todo_manager_group"].prompt_hint == "自定义提示"

    def test_yaml_can_add_new_group(self, _isolate_env) -> None:
        """yaml 可新增默认里没有的组, 同时保留全部默认组."""
        yaml_cfg = {
            "tool_groups": {
                "custom_group": {
                    "name": "custom_group",
                    "summary": "自定义组",
                    "members": ["tool_a"],
                }
            }
        }
        with patch(
            "src.config.tools_config.get_module_config_sync",
            return_value=yaml_cfg,
        ):
            config = ToolsConfig.from_module_config()
        assert "custom_group" in config.tool_groups
        assert "todo_manager_group" in config.tool_groups  # 默认组保留


class TestToolsConfigDeepMerge:
    """工具/skill 配置的 deep merge 测试."""

    @pytest.fixture
    def _isolate_env(self):
        """tools 配置不再读取通用 env overlay."""
        yield

    def test_internal_tool_config_deep_merge(self, _isolate_env) -> None:
        """internal_tools 的 config 子 dict 支持 deep merge, yaml 只写差异不丢默认."""
        yaml_cfg = {
            "internal_tools": {
                "scheduled_messenger": {
                    "config": {
                        "smtp_config": {"host": "smtp.test.com"},
                        "openclaw_defaults": {"weixin": {"channel": "test-channel"}},
                    }
                }
            }
        }
        with patch(
            "src.config.tools_config.get_module_config_sync",
            return_value=yaml_cfg,
        ):
            config = ToolsConfig.from_module_config()
        sm = config.internal_tools["scheduled_messenger"]
        # yaml 覆盖/新增的字段生效
        assert sm.config["smtp_config"]["host"] == "smtp.test.com"
        assert sm.config["openclaw_defaults"]["weixin"]["channel"] == "test-channel"
        # 默认 config 其余字段保留(deep merge 而非整体替换)
        assert sm.config["max_pending_messages"] == 50
        assert sm.config["default_channel"] == "wechat"


class TestNoneDefenseForDictCategories:
    """dict 类别字段(internal_tools/external_tools/tool_groups/mcp_servers/skills)
    在 YAML 里写成空键(None)时的防御测试.

    历史 bug: YAML 写 `internal_tools:` 紧跟下一行非缩进内容, YAML 解析为 None,
    合并逻辑只判断 isinstance(value, dict), None 走 else 分支整体覆盖默认 catalog,
    Pydantic 校验 dict 字段时崩溃, 导致 Agent 全工具丢失(生产事故根因).
    """

    @pytest.fixture
    def _isolate_env(self):
        yield

    def test_internal_tools_none_keeps_default_catalog(self, _isolate_env) -> None:
        """internal_tools: None 不应崩溃, 应保留 catalog 默认."""
        yaml_cfg = {"internal_tools": None}
        with patch(
            "src.config.tools_config.get_module_config_sync",
            return_value=yaml_cfg,
        ):
            config = ToolsConfig.from_module_config()
        # catalog 默认工具保留(未被 None 覆盖)
        assert "create_todo" in config.internal_tools
        assert "search_available_tools" in config.internal_tools

    def test_external_tools_none_keeps_default_catalog(self, _isolate_env) -> None:
        """external_tools: None 不应崩溃, 应保留 catalog 默认."""
        yaml_cfg = {"external_tools": None}
        with patch(
            "src.config.tools_config.get_module_config_sync",
            return_value=yaml_cfg,
        ):
            config = ToolsConfig.from_module_config()
        assert "export_document" in config.external_tools
        assert "python_executor" in config.external_tools

    def test_tool_groups_none_keeps_default_catalog(self, _isolate_env) -> None:
        """tool_groups: None 不应崩溃, 应保留 catalog 默认."""
        yaml_cfg = {"tool_groups": None}
        with patch(
            "src.config.tools_config.get_module_config_sync",
            return_value=yaml_cfg,
        ):
            config = ToolsConfig.from_module_config()
        assert "todo_manager_group" in config.tool_groups

    def test_mcp_servers_none_keeps_empty_default(self, _isolate_env) -> None:
        """mcp_servers: None 不应崩溃, 应保留默认(空 dict)."""
        yaml_cfg = {"mcp_servers": None}
        with patch(
            "src.config.tools_config.get_module_config_sync",
            return_value=yaml_cfg,
        ):
            config = ToolsConfig.from_module_config()
        assert config.mcp_servers == {}

    def test_skills_none_keeps_empty_default(self, _isolate_env) -> None:
        """skills: None 不应崩溃, 应保留默认(空 dict)."""
        yaml_cfg = {"skills": None}
        with patch(
            "src.config.tools_config.get_module_config_sync",
            return_value=yaml_cfg,
        ):
            config = ToolsConfig.from_module_config()
        assert config.skills == {}

    def test_multiple_dict_categories_none_simultaneously(self, _isolate_env) -> None:
        """多个 dict 类别同时为 None(生产 config.yaml 极简版场景)应全部跳过."""
        yaml_cfg = {
            "internal_tools": None,
            "external_tools": None,
            "tool_groups": None,
            "mcp_servers": None,
            "skills": None,
        }
        with patch(
            "src.config.tools_config.get_module_config_sync",
            return_value=yaml_cfg,
        ):
            config = ToolsConfig.from_module_config()
        # 全部保留 catalog 默认
        assert len(config.internal_tools) > 0
        assert len(config.external_tools) > 0
        assert len(config.tool_groups) > 0

    def test_dict_category_with_non_dict_value_warns_and_skips(
        self, _isolate_env, caplog
    ) -> None:
        """dict 类别字段给了非 dict 非 None 值(如字符串/列表)应 warn 并跳过, 不崩溃."""
        yaml_cfg = {"internal_tools": "not_a_dict"}
        with patch(
            "src.config.tools_config.get_module_config_sync",
            return_value=yaml_cfg,
        ):
            with caplog.at_level("WARNING"):
                config = ToolsConfig.from_module_config()
        # 保留默认 catalog
        assert "create_todo" in config.internal_tools
        # 有 warning 日志
        assert any(
            "internal_tools" in rec.message and "dict" in rec.message
            for rec in caplog.records
        )


class TestMemoryRecallGroupCatalog:
    """memory_recall_group 配置一致性测试 (search/fetch 职责分离的 group 化)."""

    def test_group_defined_with_search_and_fetch_members(self) -> None:
        """group 应含 search_memories(search) 和 get_round_detail(fetch) 两个 member."""
        from src.config.tool_catalog import get_builtin_tools_config

        config = get_builtin_tools_config()
        assert "memory_recall_group" in config["tool_groups"]
        members = config["tool_groups"]["memory_recall_group"]["members"]
        assert "search_memories" in members
        assert "get_round_detail" in members

    def test_group_members_all_registered_in_internal_tools(self) -> None:
        """group 的每个 member 都必须在 internal_tools 注册, 否则唤醒后无法实例化."""
        from src.config.tool_catalog import get_builtin_tools_config

        config = get_builtin_tools_config()
        members = config["tool_groups"]["memory_recall_group"]["members"]
        for member in members:
            assert member in config["internal_tools"], (
                f"{member} 未在 internal_tools 注册"
            )

    def test_group_has_wakeup_keywords(self) -> None:
        """group 应配置唤醒关键词, 否则 search_available_tools 无法按语义命中."""
        from src.config.tool_catalog import get_builtin_tools_config

        config = get_builtin_tools_config()
        keywords = config["tool_groups"]["memory_recall_group"].get("keywords", [])
        assert len(keywords) > 0

    def test_search_memories_not_in_agent_config_default_core_tools(self) -> None:
        """group 化后 search_memories 应从核心工具列表移除, 改由 group 唤醒."""
        from src.config.agent_config import AgentConfig

        cfg = AgentConfig()
        assert "search_memories" not in cfg.tools
        assert "memory_recall_group" in cfg.tools
