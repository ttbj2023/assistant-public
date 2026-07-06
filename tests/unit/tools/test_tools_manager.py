"""ToolsManager 单元测试

覆盖范围:
- __init__: 初始化逻辑
- create_tools: 工具集创建(内部/专家/MCP)
- health_check: 健康检查(正常/异常)
- _get_or_create_tool: 缓存策略(内部缓存/专家缓存/MCP透传)
- _is_internal_tool / _is_expert_tool: 工具类型判断
- _create_internal_tool: 内部工具动态创建
- get_cache_stats: 缓存统计
- clear_cache: 缓存清理
- get_tool_stats: 工具统计
- list_available_tools: 可用工具列表
- get_tool_config_info: 工具配置查询
- build_tools: 兼容接口
- get_tools_manager: 全局单例
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.config.tools_config import InternalToolConfig, McpServerConfig
from src.tools.tools_manager import ToolsManager, get_tools_manager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_internal_tool_config(
    name: str = "test_tool",
    class_path: str = "src.tools.internal.test_tool.TestTool",
    enabled: bool = True,
    timeout: float = 30.0,
    description: str = "测试工具",
    config: dict | None = None,
) -> InternalToolConfig:
    """快速构造 InternalToolConfig"""
    return InternalToolConfig(
        name=name,
        class_path=class_path,
        enabled=enabled,
        timeout=timeout,
        description=description,
        config=config or {},
    )


def _make_mcp_server_config(
    name: str = "test_server",
    transport: str = "stdio",
    enabled: bool = True,
    tool_names: dict | None = None,
) -> McpServerConfig:
    """快速构造 McpServerConfig"""
    return McpServerConfig(
        name=name,
        transport=transport,
        enabled=enabled,
        tool_names=tool_names or {"raw_tool": "project_tool"},
    )


@pytest.fixture
def mock_tools_config():
    """Mock 的 ToolsConfig 对象"""
    cfg = MagicMock()
    cfg.mcp_servers = {}
    return cfg


@pytest.fixture
def mock_mcp_bridge():
    """Mock 的 McpBridge 对象"""
    bridge = MagicMock()
    bridge._tools = {}
    bridge.health_check = AsyncMock(return_value={"healthy": True, "servers": {}})
    bridge.get_tool = AsyncMock(return_value=None)
    bridge.get_all_tools = AsyncMock(return_value=[])
    bridge.reload = AsyncMock(return_value=None)
    bridge.close = AsyncMock(return_value=None)
    bridge.get_stats = Mock(return_value={"total_tools": 0})
    return bridge


@pytest.fixture
def manager(mock_tools_config, mock_mcp_bridge):
    """构造一个经过 Mock 注入的 ToolsManager 实例

    通过 patch 绕过 get_config("tools") 和 McpBridge() 构造函数,
    使 ToolsManager 在初始化时使用我们注入的 mock 对象。
    """
    with (
        patch("src.tools.tools_manager.get_config", return_value=mock_tools_config),
        patch("src.tools.tools_manager.McpBridge", return_value=mock_mcp_bridge),
    ):
        mgr = ToolsManager()

    # 直接引用, 方便 fixture 使用者直接操作 mock
    mgr._mock_tools_config = mock_tools_config
    mgr._mock_mcp_bridge = mock_mcp_bridge
    return mgr


@pytest.fixture
def fake_base_tool():
    """构造一个假的 BaseTool 实例"""
    tool = MagicMock()
    tool.name = "fake_tool"
    tool.is_available = AsyncMock(return_value=True)
    return tool


# ===========================================================================
# create_tools
# ===========================================================================


class TestCreateTools:
    """create_tools 核心流程测试"""

    @pytest.mark.asyncio
    async def test_create_tools_empty_names_returns_empty(self, manager):
        """空工具名列表应返回空列表"""
        result = await manager.create_tools([], "user1", "thread1", agent_id="agent1")
        assert result == []

    @pytest.mark.asyncio
    async def test_create_tools_caches_internal_tool(self, manager, fake_base_tool):
        """内部工具应被缓存到 _internal_cache 中"""
        # Arrange: 设置一个启用的内部工具
        tool_cfg = _make_internal_tool_config(name="cached_tool")
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]

        # mock _create_internal_tool 返回假工具
        manager._create_internal_tool = AsyncMock(return_value=fake_base_tool)

        # Act: 第一次调用
        result = await manager.create_tools(
            ["cached_tool"], "user1", "thread1", agent_id="agent1"
        )

        # Assert: 工具创建成功
        assert len(result) == 1
        assert result[0] is fake_base_tool

        # 验证缓存: cache_key = "user1:thread1:agent1"
        cache_key = "user1:thread1:agent1"
        assert "cached_tool" in manager._internal_cache[cache_key]

    @pytest.mark.asyncio
    async def test_create_tools_reuses_internal_cache(self, manager, fake_base_tool):
        """第二次请求同一内部工具应复用缓存而非重新创建"""
        tool_cfg = _make_internal_tool_config(name="reuse_tool")
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]

        create_mock = AsyncMock(return_value=fake_base_tool)
        manager._create_internal_tool = create_mock

        # 第一次
        await manager.create_tools(["reuse_tool"], "u", "t", agent_id="a")
        # 第二次
        result2 = await manager.create_tools(["reuse_tool"], "u", "t", agent_id="a")

        # _create_internal_tool 应只调用一次(第二次走缓存)
        assert create_mock.call_count == 1
        assert len(result2) == 1

    @pytest.mark.asyncio
    async def test_create_tools_different_agent_different_cache(
        self, manager, fake_base_tool
    ):
        """不同 agent_id 应使用不同的缓存槽"""
        tool_cfg = _make_internal_tool_config(name="multi_tool")
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]
        manager._create_internal_tool = AsyncMock(return_value=fake_base_tool)

        await manager.create_tools(["multi_tool"], "u", "t", agent_id="a1")
        await manager.create_tools(["multi_tool"], "u", "t", agent_id="a2")

        assert "u:t:a1" in manager._internal_cache
        assert "u:t:a2" in manager._internal_cache

    @pytest.mark.asyncio
    async def test_create_tools_exception_skips_tool(self, manager):
        """单个工具创建异常不应影响其他工具"""
        tool_cfg = _make_internal_tool_config(name="bad_tool")
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]

        # _get_or_create_tool 对 bad_tool 抛异常
        manager._get_or_create_tool = AsyncMock(side_effect=RuntimeError("boom"))

        result = await manager.create_tools(["bad_tool"], "u", "t", agent_id="a")
        assert result == []

    @pytest.mark.asyncio
    async def test_create_tools_mixed_success_and_failure(
        self, manager, fake_base_tool
    ):
        """部分工具成功、部分失败时只返回成功的工具"""
        tool_cfg = _make_internal_tool_config(name="ok_tool")
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]

        call_count = 0

        async def _side_effect(name, uid, tid, ck, *, agent_id):
            nonlocal call_count
            call_count += 1
            if name == "ok_tool":
                return fake_base_tool
            return None

        manager._get_or_create_tool = AsyncMock(side_effect=_side_effect)

        result = await manager.create_tools(
            ["ok_tool", "missing_tool"], "u", "t", agent_id="a"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_create_tools_none_return_skipped(self, manager):
        """_get_or_create_tool 返回 None 时该工具应被跳过"""
        manager._get_or_create_tool = AsyncMock(return_value=None)

        result = await manager.create_tools(["ghost"], "u", "t", agent_id="a")
        assert result == []


# ===========================================================================
# _get_or_create_tool
# ===========================================================================


class TestGetOrCreateTool:
    """_get_or_create_tool 智能路由和缓存策略测试"""

    @pytest.mark.asyncio
    async def test_internal_tool_returns_from_cache(self, manager, fake_base_tool):
        """内部工具已缓存时直接返回"""
        tool_cfg = _make_internal_tool_config(name="int_tool")
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]
        manager._internal_cache["u:t:a"] = {"int_tool": fake_base_tool}

        result = await manager._get_or_create_tool(
            "int_tool", "u", "t", "u:t:a", agent_id="a"
        )
        assert result is fake_base_tool

    @pytest.mark.asyncio
    async def test_internal_tool_creates_and_caches(self, manager, fake_base_tool):
        """内部工具未缓存时创建并存入缓存"""
        tool_cfg = _make_internal_tool_config(name="new_int")
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]
        manager._create_internal_tool = AsyncMock(return_value=fake_base_tool)
        manager._internal_cache["u:t:a"] = {}

        result = await manager._get_or_create_tool(
            "new_int", "u", "t", "u:t:a", agent_id="a"
        )
        assert result is fake_base_tool
        assert manager._internal_cache["u:t:a"]["new_int"] is fake_base_tool

    @pytest.mark.asyncio
    async def test_internal_tool_create_returns_none(self, manager):
        """内部工具创建失败(返回 None)不写入缓存"""
        tool_cfg = _make_internal_tool_config(name="fail_int")
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]
        manager._create_internal_tool = AsyncMock(return_value=None)
        manager._internal_cache["u:t:a"] = {}

        result = await manager._get_or_create_tool(
            "fail_int", "u", "t", "u:t:a", agent_id="a"
        )
        assert result is None
        assert "fail_int" not in manager._internal_cache["u:t:a"]

    @pytest.mark.asyncio
    async def test_expert_tool_returns_from_cache(self, manager, fake_base_tool):
        """专家工具已缓存时直接返回"""
        manager._expert_tools_cache["web_research"] = fake_base_tool

        result = await manager._get_or_create_tool(
            "web_research", "u", "t", "u:t:a", agent_id="a"
        )
        assert result is fake_base_tool

    @pytest.mark.asyncio
    async def test_expert_tool_creates_and_caches(self, manager, fake_base_tool):
        """专家工具首次创建并缓存"""
        mock_experts_config = MagicMock()
        mock_experts_config.get_model_id.return_value = "test-model"
        with (
            patch("src.tools.tools_manager.create_expert_tools") as mock_create,
            patch(
                "src.config.inference_config.get_config",
                return_value=MagicMock(experts=mock_experts_config),
            ),
        ):
            mock_create.return_value = [fake_base_tool]

            result = await manager._get_or_create_tool(
                "web_research", "u", "t", "u:t:a", agent_id="a"
            )
            assert result is fake_base_tool
            assert manager._expert_tools_cache["web_research"] is fake_base_tool
            mock_create.assert_called_once_with(
                ["web_research"],
                mcp_bridge=manager._mock_mcp_bridge,
                model_id="test-model",
            )

    @pytest.mark.asyncio
    async def test_expert_tool_create_empty_list_returns_none(self, manager):
        """专家工具创建返回空列表时应返回 None"""
        with patch("src.tools.tools_manager.create_expert_tools") as mock_create:
            mock_create.return_value = []

            result = await manager._get_or_create_tool(
                "geo_navigator", "u", "t", "u:t:a", agent_id="a"
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_mcp_tool_delegates_to_bridge(self, manager, fake_base_tool):
        """非内部/专家工具应委托给 McpBridge"""
        manager._mock_tools_config.list_enabled_internal_tools.return_value = []
        manager._mock_mcp_bridge.get_tool = AsyncMock(return_value=fake_base_tool)

        result = await manager._get_or_create_tool(
            "some_mcp_tool", "u", "t", "u:t:a", agent_id="a"
        )
        assert result is fake_base_tool
        manager._mock_mcp_bridge.get_tool.assert_awaited_once_with("some_mcp_tool")

    @pytest.mark.asyncio
    async def test_mcp_tool_not_found_returns_none(self, manager):
        """MCP 工具未找到时返回 None"""
        manager._mock_tools_config.list_enabled_internal_tools.return_value = []
        manager._mock_mcp_bridge.get_tool = AsyncMock(return_value=None)

        result = await manager._get_or_create_tool(
            "missing_mcp", "u", "t", "u:t:a", agent_id="a"
        )
        assert result is None


# ===========================================================================
# _is_internal_tool / _is_expert_tool
# ===========================================================================


class TestToolTypeDetection:
    """工具类型判断测试"""

    def test_is_internal_tool_true(self, manager):
        """匹配到启用的内部工具时返回 True"""
        tool_cfg = _make_internal_tool_config(name="my_tool")
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]

        assert manager._is_internal_tool("my_tool") is True

    def test_is_internal_tool_false(self, manager):
        """不匹配时返回 False"""
        manager._mock_tools_config.list_enabled_internal_tools.return_value = []

        assert manager._is_internal_tool("not_exist") is False

    def test_is_expert_tool_true(self, manager):
        """web_research 和 geo_research 是专家工具"""
        assert manager._is_expert_tool("web_research") is True
        assert manager._is_expert_tool("geo_navigator") is True

    def test_is_expert_tool_false(self, manager):
        """非专家工具名返回 False"""
        assert manager._is_expert_tool("something_else") is False
        assert manager._is_expert_tool("generate_image") is False


# ===========================================================================
# _create_internal_tool
# ===========================================================================


class TestCreateInternalTool:
    """内部工具动态创建测试"""

    @pytest.mark.asyncio
    async def test_create_success(self, manager, fake_base_tool):
        """正常路径: 配置有效 + 安全路径 + 动态导入成功"""
        tool_cfg = _make_internal_tool_config(
            name="good_tool",
            class_path="src.tools.internal.good.GoodTool",
        )
        manager._mock_tools_config.get_internal_tool_config.return_value = tool_cfg

        mock_tool_class = Mock(return_value=fake_base_tool)
        mock_sanitizer = MagicMock()
        mock_sanitizer.is_safe_class_path.return_value = True
        mock_sanitizer.safe_import.return_value = mock_tool_class

        with patch.dict(
            "sys.modules",
            {
                "src.core.validation.unified_sanitizer": MagicMock(
                    UnifiedSanitizer=mock_sanitizer
                )
            },
        ):
            result = await manager._create_internal_tool(
                "good_tool", "u", "t", agent_id="a"
            )

        assert result is fake_base_tool
        mock_tool_class.assert_called_once_with("u", "t", agent_id="a")
        mock_sanitizer.is_safe_class_path.assert_called_once_with(
            "src.tools.internal.good.GoodTool"
        )
        mock_sanitizer.safe_import.assert_called_once_with(
            "src.tools.internal.good", "GoodTool"
        )

    @pytest.mark.asyncio
    async def test_create_disabled_tool_returns_none(self, manager):
        """工具未启用时应返回 None"""
        tool_cfg = _make_internal_tool_config(name="disabled", enabled=False)
        manager._mock_tools_config.get_internal_tool_config.return_value = tool_cfg

        result = await manager._create_internal_tool("disabled", "u", "t", agent_id="a")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_no_config_returns_none(self, manager):
        """工具配置不存在时返回 None"""
        manager._mock_tools_config.get_internal_tool_config.return_value = None

        result = await manager._create_internal_tool("missing", "u", "t", agent_id="a")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_no_class_path_returns_none(self, manager):
        """class_path 为空时应返回 None"""
        tool_cfg = _make_internal_tool_config(name="no_path")
        tool_cfg.class_path = ""
        manager._mock_tools_config.get_internal_tool_config.return_value = tool_cfg

        result = await manager._create_internal_tool("no_path", "u", "t", agent_id="a")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_unsafe_class_path_returns_none(self, manager):
        """不安全的类路径应返回 None"""
        tool_cfg = _make_internal_tool_config(name="unsafe", class_path="os.system")
        manager._mock_tools_config.get_internal_tool_config.return_value = tool_cfg

        mock_sanitizer = MagicMock()
        mock_sanitizer.is_safe_class_path.return_value = False

        with patch.dict(
            "sys.modules",
            {
                "src.core.validation.unified_sanitizer": MagicMock(
                    UnifiedSanitizer=mock_sanitizer
                )
            },
        ):
            result = await manager._create_internal_tool(
                "unsafe", "u", "t", agent_id="a"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_create_import_exception_raises(self, manager):
        """动态导入异常(配置错误)应直接抛出, 而非静默返回 None"""
        tool_cfg = _make_internal_tool_config(
            name="bad_import", class_path="nonexistent.module.BadTool"
        )
        manager._mock_tools_config.get_internal_tool_config.return_value = tool_cfg

        mock_sanitizer = MagicMock()
        mock_sanitizer.is_safe_class_path.return_value = True
        mock_sanitizer.safe_import.side_effect = ImportError("no module")

        with (
            patch.dict(
                "sys.modules",
                {
                    "src.core.validation.unified_sanitizer": MagicMock(
                        UnifiedSanitizer=mock_sanitizer
                    )
                },
            ),
            pytest.raises(ImportError, match="no module"),
        ):
            await manager._create_internal_tool("bad_import", "u", "t", agent_id="a")

    @pytest.mark.asyncio
    async def test_create_passes_extra_config(self, manager, fake_base_tool):
        """工具配置中的额外参数应传递给工具构造函数"""
        tool_cfg = _make_internal_tool_config(
            name="cfg_tool",
            class_path="src.tools.internal.cfg.CfgTool",
            config={"max_items": 50, "debug": True},
        )
        manager._mock_tools_config.get_internal_tool_config.return_value = tool_cfg

        mock_tool_class = Mock(return_value=fake_base_tool)
        mock_sanitizer = MagicMock()
        mock_sanitizer.is_safe_class_path.return_value = True
        mock_sanitizer.safe_import.return_value = mock_tool_class

        with patch.dict(
            "sys.modules",
            {
                "src.core.validation.unified_sanitizer": MagicMock(
                    UnifiedSanitizer=mock_sanitizer
                )
            },
        ):
            await manager._create_internal_tool("cfg_tool", "u", "t", agent_id="a")

        mock_tool_class.assert_called_once_with(
            "u", "t", agent_id="a", max_items=50, debug=True
        )


# ===========================================================================
# health_check
# ===========================================================================


class TestHealthCheck:
    """健康检查测试"""

    @pytest.mark.asyncio
    async def test_all_healthy(self, manager):
        """所有工具和 MCP 服务器健康时整体返回 healthy=True"""
        tool_cfg = _make_internal_tool_config(name="alive_tool")
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]
        manager._mock_tools_config.get_internal_tool_config.return_value = tool_cfg
        manager._mock_mcp_bridge.health_check = AsyncMock(
            return_value={"healthy": True, "servers": {}}
        )

        result = await manager.health_check()

        assert result["healthy"] is True
        assert result["tools"]["alive_tool"]["healthy"] is True
        assert result["tools"]["alive_tool"]["type"] == "internal"

    @pytest.mark.asyncio
    async def test_disabled_tool_marks_unhealthy(self, manager):
        """禁用的内部工具应导致整体 healthy=False"""
        tool_cfg = _make_internal_tool_config(name="dead_tool", enabled=False)
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]
        manager._mock_tools_config.get_internal_tool_config.return_value = tool_cfg
        manager._mock_mcp_bridge.health_check = AsyncMock(
            return_value={"healthy": True, "servers": {}}
        )

        result = await manager.health_check()

        assert result["healthy"] is False
        assert result["tools"]["dead_tool"]["healthy"] is False

    @pytest.mark.asyncio
    async def test_no_config_tool_marks_unhealthy(self, manager):
        """配置查询返回 None 时该工具标记为不健康"""
        tool_cfg = _make_internal_tool_config(name="ghost_tool")
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]
        manager._mock_tools_config.get_internal_tool_config.return_value = None
        manager._mock_mcp_bridge.health_check = AsyncMock(
            return_value={"healthy": True, "servers": {}}
        )

        result = await manager.health_check()

        assert result["healthy"] is False
        assert result["tools"]["ghost_tool"]["healthy"] is False

    @pytest.mark.asyncio
    async def test_exception_in_tool_marks_unhealthy(self, manager):
        """内部工具检查抛异常时整体 healthy=False"""
        tool_cfg = _make_internal_tool_config(name="err_tool")
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]
        manager._mock_tools_config.get_internal_tool_config.side_effect = RuntimeError(
            "db error"
        )
        manager._mock_mcp_bridge.health_check = AsyncMock(
            return_value={"healthy": True, "servers": {}}
        )

        result = await manager.health_check()

        assert result["healthy"] is False
        assert "error" in result["tools"]["err_tool"]

    @pytest.mark.asyncio
    async def test_mcp_server_error_marks_unhealthy(self, manager):
        """MCP 服务器异常时整体 healthy=False"""
        manager._mock_tools_config.list_enabled_internal_tools.return_value = []
        manager._mock_mcp_bridge.health_check = AsyncMock(
            return_value={
                "healthy": False,
                "servers": {"bad_server": {"status": "error", "detail": "conn fail"}},
            }
        )

        result = await manager.health_check()

        assert result["healthy"] is False
        assert result["tools"]["mcp:bad_server"]["type"] == "mcp"
        assert result["tools"]["mcp:bad_server"]["healthy"] is False

    @pytest.mark.asyncio
    async def test_mcp_server_ok_marks_healthy(self, manager):
        """MCP 服务器正常时该条目 healthy=True"""
        manager._mock_tools_config.list_enabled_internal_tools.return_value = []
        manager._mock_mcp_bridge.health_check = AsyncMock(
            return_value={
                "healthy": True,
                "servers": {"ok_server": {"status": "configured", "tool_count": 1}},
            }
        )

        result = await manager.health_check()

        assert result["tools"]["mcp:ok_server"]["healthy"] is True

    @pytest.mark.asyncio
    async def test_cache_stats_in_result(self, manager):
        """返回结果应包含 cache_stats"""
        manager._mock_tools_config.list_enabled_internal_tools.return_value = []
        manager._mock_mcp_bridge.health_check = AsyncMock(
            return_value={"healthy": True, "servers": {}}
        )

        result = await manager.health_check()

        assert "cache_stats" in result
        assert "internal_cache_entries" in result["cache_stats"]
        assert "mcp_tools_loaded" in result["cache_stats"]
        assert "total_user_sessions" in result["cache_stats"]


# ===========================================================================
# get_cache_stats
# ===========================================================================


class TestGetCacheStats:
    """缓存统计信息测试"""

    def test_empty_cache(self, manager):
        """空缓存时返回零值统计"""
        stats = manager.get_cache_stats()

        assert stats["internal_tools"]["user_sessions"] == 0
        assert stats["internal_tools"]["total_tools"] == 0

    def test_cache_with_entries(self, manager, fake_base_tool):
        """有缓存条目时统计正确"""
        manager._internal_cache = {
            "u1:t1:a1": {"tool_a": fake_base_tool, "tool_b": fake_base_tool},
            "u2:t2:a2": {"tool_c": fake_base_tool},
        }
        manager._mock_mcp_bridge.get_stats.return_value = {
            "total_tools": 2,
            "tool_names": ["mcp_1", "mcp_2"],
        }

        stats = manager.get_cache_stats()

        assert stats["internal_tools"]["user_sessions"] == 2
        assert stats["internal_tools"]["total_tools"] == 3
        assert "u1:t1:a1" in stats["internal_tools"]["tools_by_session"]
        assert stats["mcp_tools"]["total_tools"] == 2


# ===========================================================================
# clear_cache
# ===========================================================================


class TestClearCache:
    """缓存清理测试"""

    @pytest.mark.asyncio
    async def test_clears_internal_cache(self, manager, fake_base_tool):
        """清理后 _internal_cache 应为空"""
        manager._internal_cache = {"k": {"t": fake_base_tool}}

        await manager.clear_cache()

        assert manager._internal_cache == {}
        manager._mock_mcp_bridge.reload.assert_awaited_once()


# ===========================================================================
# close (优雅关闭)
# ===========================================================================


class TestClose:
    """close() 优雅关闭测试 - 释放MCP连接并清空全部缓存."""

    @pytest.mark.asyncio
    async def test_close_calls_mcp_bridge_close(self, manager):
        """close() 应调用 _mcp_bridge.close() 释放MCP连接(含stdio子进程)."""
        await manager.close()

        manager._mock_mcp_bridge.close.assert_awaited_once()
        # close() 不应触发 reload (reload面向刷新, close面向关闭)
        manager._mock_mcp_bridge.reload.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_clears_all_three_caches(self, manager, fake_base_tool):
        """close() 应清空 internal/expert/external 三类缓存."""
        manager._internal_cache = {"u:t:a": {"t1": fake_base_tool}}
        manager._expert_tools_cache = {"web_research": fake_base_tool}
        manager._external_tools_cache = {"weather_query": fake_base_tool}

        await manager.close()

        assert manager._internal_cache == {}
        assert manager._expert_tools_cache == {}
        assert manager._external_tools_cache == {}

    @pytest.mark.asyncio
    async def test_close_tolerates_mcp_close_exception(self, manager, fake_base_tool):
        """_mcp_bridge.close() 抛异常时不应中断, 缓存仍应清空."""
        manager._mock_mcp_bridge.close = AsyncMock(side_effect=RuntimeError("boom"))
        manager._internal_cache = {"u:t:a": {"t1": fake_base_tool}}
        manager._expert_tools_cache = {"web_research": fake_base_tool}

        await manager.close()  # 不应抛出

        assert manager._internal_cache == {}
        assert manager._expert_tools_cache == {}

    @pytest.mark.asyncio
    async def test_close_idempotent_on_empty_state(self, manager):
        """空状态close()不应报错(未初始化MCP等场景)."""
        await manager.close()

        assert manager._internal_cache == {}
        manager._mock_mcp_bridge.close.assert_awaited_once()


# ===========================================================================
# get_tool_stats
# ===========================================================================


class TestGetToolStats:
    """工具统计接口测试"""

    def test_stats_structure(self, manager):
        """返回结构应包含所有必要字段"""
        tool_cfg = _make_internal_tool_config(name="s_tool")
        manager._mock_tools_config.list_enabled_internal_tools.return_value = [tool_cfg]
        manager._mock_mcp_bridge._tools = {"m1": MagicMock(), "m2": MagicMock()}

        stats = manager.get_tool_stats()

        assert stats["internal_tools"] == 1
        assert stats["mcp_tools"] == 2
        assert stats["total_tools"] == 3
        assert stats["active_connections"] == 0
        assert "cache_stats" in stats

    def test_stats_empty(self, manager):
        """无工具时统计为零"""
        manager._mock_tools_config.list_enabled_internal_tools.return_value = []
        manager._mock_mcp_bridge._tools = {}

        stats = manager.get_tool_stats()

        assert stats["total_tools"] == 0


# ===========================================================================
# get_tools_manager (全局单例)
# ===========================================================================


class TestGetToolsManager:
    """全局单例获取测试"""

    def test_returns_same_instance(self):
        """多次调用应返回同一实例(单例模式)"""
        import src.tools.tools_manager as mod

        # 重置全局状态
        mod._tools_manager = None

        with (
            patch("src.tools.tools_manager.get_config") as mock_cfg,
            patch("src.tools.tools_manager.McpBridge"),
        ):
            mock_cfg.return_value = MagicMock(mcp_servers={})

            inst1 = get_tools_manager()
            inst2 = get_tools_manager()

        assert inst1 is inst2

        # 清理
        mod._tools_manager = None


# ===========================================================================
# create_dormant_tools
# ===========================================================================


class TestCreateDormantTools:
    """休眠工具池创建测试"""

    @pytest.mark.asyncio
    async def test_empty_names_should_return_empty(self, manager):
        result = await manager.create_dormant_tools([], "u", "t", agent_id="a")
        assert result == []

    @pytest.mark.asyncio
    async def test_should_create_available_tools(self, manager, fake_base_tool):
        fake_base_tool.is_available = AsyncMock(return_value=True)
        manager._get_or_create_tool = AsyncMock(return_value=fake_base_tool)
        result = await manager.create_dormant_tools(
            ["fake_tool"], "u", "t", agent_id="a"
        )
        assert len(result) == 1
        assert result[0].name == "fake_tool"

    @pytest.mark.asyncio
    async def test_should_skip_unavailable_tools(self, manager):
        unavailable_tool = MagicMock()
        unavailable_tool.name = "unavailable_tool"
        unavailable_tool.is_available = AsyncMock(return_value=False)

        manager._get_or_create_tool = AsyncMock(return_value=unavailable_tool)
        result = await manager.create_dormant_tools(
            ["unavailable_tool"], "u", "t", agent_id="a"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_should_not_skip_mcp_tools_without_is_available(
        self, manager, fake_base_tool
    ):
        """MCP工具(StructuredTool)无is_available属性时不应被跳过"""
        del fake_base_tool.is_available
        manager._get_or_create_tool = AsyncMock(return_value=fake_base_tool)
        result = await manager.create_dormant_tools(
            ["fake_tool"], "u", "t", agent_id="a"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_should_continue_on_creation_error(self, manager):
        ok_tool = MagicMock()
        ok_tool.name = "ok_tool"
        ok_tool.is_available = AsyncMock(return_value=True)

        manager._get_or_create_tool = AsyncMock(
            side_effect=[RuntimeError("创建失败"), ok_tool]
        )
        result = await manager.create_dormant_tools(
            ["fail_tool", "ok_tool"], "u", "t", agent_id="a"
        )
        assert len(result) == 1
        assert result[0].name == "ok_tool"

    @pytest.mark.asyncio
    async def test_should_use_cache_key_with_agent_id(self, manager, fake_base_tool):
        fake_base_tool.is_available = AsyncMock(return_value=True)
        manager._get_or_create_tool = AsyncMock(return_value=fake_base_tool)
        await manager.create_dormant_tools(
            ["fake_tool"], "u1", "t1", agent_id="agent_a"
        )
        assert "u1:t1:agent_a" in manager._internal_cache
