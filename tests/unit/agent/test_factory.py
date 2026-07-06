"""AgentFactory单元测试.

测试职责: 验证Agent工厂的创建、配置加载、错误处理
Mock策略: Mock所有外部依赖(模型加载器、配置、DAO等), 保留AgentFactory真实业务逻辑
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.agent.factory import AgentFactory

# ==================== AgentFactory 初始化测试 ====================


class TestAgentFactoryInit:
    """测试AgentFactory初始化"""

    def test_init_default_dir_should_use_default_path(self):
        """默认参数: 应使用默认implementations_dir"""
        factory = AgentFactory()

        assert factory.implementations_dir == Path("src/agent/agents_implementations")

    def test_init_custom_dir_should_use_custom_path(self):
        """自定义参数: 应使用自定义路径"""
        factory = AgentFactory(implementations_dir="/custom/path")

        assert factory.implementations_dir == Path("/custom/path")


# ==================== create_agent 测试 ====================


class TestCreateAgent:
    """测试create_agent完整流程"""

    @pytest.fixture
    def factory(self, tmp_path):
        """创建使用临时目录的factory"""
        return AgentFactory(implementations_dir=str(tmp_path))

    @pytest.fixture
    def mock_config_dir(self, tmp_path):
        """创建模拟的agent配置目录"""
        config_dir = tmp_path / "personal_assistant"
        config_dir.mkdir()
        yaml_content = {
            "agent_id": "personal-assistant",
            "name": "Test Agent",
            "model_id": "test-model",
            "system_prompt": "test prompt",
            "tools": ["create_todo"],
            "memory": {"type": "local", "total_char_budget": 20000},
        }
        import yaml

        (config_dir / "agent.yaml").write_text(
            yaml.dump(yaml_content), encoding="utf-8"
        )
        return config_dir

    @pytest.mark.asyncio
    async def test_create_agent_success_should_return_initialized_agent(
        self, factory, mock_config_dir
    ):
        """正常流程: 应返回已初始化的Agent实例"""
        mock_agent_instance = AsyncMock()
        mock_agent_class = Mock(return_value=mock_agent_instance)
        mock_config = Mock()
        mock_config.agent_id = "personal-assistant"
        mock_config.tools = ["create_todo"]

        with (
            patch(
                "src.agent.factory.get_agent_directory",
                return_value="personal_assistant",
            ),
            patch(
                "src.agent.factory.AgentFactory._load_agent_config",
                return_value=mock_config,
            ),
            patch("src.agent.factory.AgentFactory._validate_tool_names"),
            patch(
                "src.agent.factory.AgentFactory._load_agent_class",
                return_value=mock_agent_class,
            ),
        ):
            result = await factory.create_agent("personal-assistant")

            assert result is mock_agent_instance
            mock_agent_instance.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_agent_config_not_found_should_raise(self, factory):
        """配置文件不存在: 应抛出FileNotFoundError"""
        with patch("src.agent.factory.get_agent_directory", return_value="nonexistent"):
            with pytest.raises(FileNotFoundError, match="Agent配置文件不存在"):
                await factory.create_agent("nonexistent-agent")

    @pytest.mark.parametrize(
        "mock_dir,invalid_model_id",
        [
            # startswith("gpt-") 检测 — 对应 openai:gpt-5.5 的裸格式误用
            ("gpt_5.5", "gpt-5.5"),
            # startswith("claude-") 检测 — 保留前缀检测覆盖
            ("claude_sonnet_4", "claude-sonnet-4"),
            # ":" 冒号检测 — 标准 provider:model 格式（对应 deepseek:deepseek-v4-flash）
            ("deepseek:deepseek_v4_flash", "deepseek:deepseek-v4-flash"),
            # "local:" 检测 — 本地模型格式（对应 local:qwen3.5:9b）
            ("local:qwen3.5:9b", "local:qwen3.5:9b"),
            # startswith("gemini-") 检测 — 对应 gemini:gemini-3.5-flash 的裸格式误用
            ("gemini_3.5_flash", "gemini-3.5-flash"),
            # startswith("zhipu-") 检测 — 保留前缀检测覆盖
            ("zhipu_glm4", "zhipu-glm4"),
        ],
    )
    @pytest.mark.asyncio
    async def test_create_agent_model_id_misuse_should_raise_with_hint(
        self, factory, mock_dir, invalid_model_id
    ):
        """模型ID误用(各前缀): 应抛出包含提示信息的FileNotFoundError"""
        with patch("src.agent.factory.get_agent_directory", return_value=mock_dir):
            with patch.object(
                factory, "get_supported_agents", return_value=["personal-assistant"]
            ):
                with pytest.raises(FileNotFoundError, match="模型名称"):
                    await factory.create_agent(invalid_model_id)

    @pytest.mark.asyncio
    async def test_create_agent_model_misuse_should_show_available_agents(
        self, factory
    ):
        """模型误用时: 错误信息应包含可用Agent列表"""
        with patch("src.agent.factory.get_agent_directory", return_value="gpt_4"):
            with patch.object(
                factory,
                "get_supported_agents",
                return_value=["personal-assistant", "health-assistant"],
            ):
                with pytest.raises(FileNotFoundError) as exc_info:
                    await factory.create_agent("gpt-4")

                error_msg = str(exc_info.value)
                assert "personal-assistant" in error_msg

    @pytest.mark.asyncio
    async def test_create_agent_model_misuse_no_available_agents_should_not_crash(
        self, factory
    ):
        """模型误用且无可用Agent: 不应崩溃"""
        with patch("src.agent.factory.get_agent_directory", return_value="gpt_4"):
            with patch.object(factory, "get_supported_agents", return_value=[]):
                with pytest.raises(FileNotFoundError, match="模型名称"):
                    await factory.create_agent("gpt-4")

    @pytest.mark.asyncio
    async def test_create_agent_model_misuse_many_agents_should_truncate(self, factory):
        """模型误用且可用Agent超过3个: 只显示前3个"""
        agents = [f"agent-{i}" for i in range(5)]
        with patch("src.agent.factory.get_agent_directory", return_value="gpt_4"):
            with patch.object(factory, "get_supported_agents", return_value=agents):
                with pytest.raises(FileNotFoundError) as exc_info:
                    await factory.create_agent("gpt-4")

                error_msg = str(exc_info.value)
                assert "agent-0" in error_msg
                assert "..." in error_msg

    @pytest.mark.asyncio
    async def test_create_agent_config_load_failure_should_raise(
        self, factory, tmp_path
    ):
        """配置加载失败: 应向上抛出异常"""
        config_dir = tmp_path / "personal_assistant"
        config_dir.mkdir()
        (config_dir / "agent.yaml").write_text("valid: true", encoding="utf-8")

        with (
            patch(
                "src.agent.factory.get_agent_directory",
                return_value="personal_assistant",
            ),
            patch(
                "src.agent.factory.AgentFactory._load_agent_config",
                side_effect=ValueError("配置验证失败"),
            ),
            pytest.raises(ValueError, match="配置验证失败"),
        ):
            await factory.create_agent("personal-assistant")

    @pytest.mark.asyncio
    async def test_create_agent_initialize_failure_should_raise(
        self, factory, mock_config_dir
    ):
        """Agent初始化失败: 应向上抛出异常"""
        mock_agent_instance = AsyncMock()
        mock_agent_instance.initialize.side_effect = RuntimeError("初始化失败")
        mock_agent_class = Mock(return_value=mock_agent_instance)
        mock_config = Mock()
        mock_config.agent_id = "personal-assistant"
        mock_config.tools = ["create_todo"]

        with (
            patch(
                "src.agent.factory.get_agent_directory",
                return_value="personal_assistant",
            ),
            patch(
                "src.agent.factory.AgentFactory._load_agent_config",
                return_value=mock_config,
            ),
            patch("src.agent.factory.AgentFactory._validate_tool_names"),
            patch(
                "src.agent.factory.AgentFactory._load_agent_class",
                return_value=mock_agent_class,
            ),
            pytest.raises(RuntimeError, match="初始化失败"),
        ):
            await factory.create_agent("personal-assistant")


# ==================== _load_agent_config 测试 ====================


class TestLoadAgentConfig:
    """测试_load_agent_config静态方法 - yaml读取后由Pydantic解析, 缺少字段用默认值兜底"""

    @pytest.mark.asyncio
    async def test_load_config_full_yaml_should_return_config(self, tmp_path):
        """完整YAML: 应返回AgentConfig, yaml值优先"""
        yaml_content = (
            "agent_id: personal-assistant\n"
            "name: Overridden Name\n"
            "model_id: test-model\n"
        )
        config_file = tmp_path / "agent.yaml"
        config_file.write_text(yaml_content, encoding="utf-8")

        result = await AgentFactory._load_agent_config(str(config_file))

        from src.config.agent_config import AgentConfig

        assert isinstance(result, AgentConfig)
        assert result.name == "Overridden Name"
        assert result.model_id == "test-model"

    @pytest.mark.asyncio
    async def test_load_config_empty_yaml_should_use_defaults(self, tmp_path):
        """空YAML文件: 应完全使用Pydantic默认值"""
        config_file = tmp_path / "agent.yaml"
        config_file.write_text("", encoding="utf-8")

        result = await AgentFactory._load_agent_config(str(config_file))

        assert isinstance(result.name, str) and result.name
        assert isinstance(result.model_id, str) and ":" in result.model_id
        assert result.memory.total_char_budget > 0

    @pytest.mark.asyncio
    async def test_load_config_partial_memory_should_merge_with_defaults(
        self, tmp_path
    ):
        """YAML只覆盖memory部分字段: 未覆盖字段保留Pydantic默认值"""
        yaml_content = "memory:\n  total_char_budget: 30000\n"
        config_file = tmp_path / "agent.yaml"
        config_file.write_text(yaml_content, encoding="utf-8")

        result = await AgentFactory._load_agent_config(str(config_file))

        assert result.memory.total_char_budget == 30000
        assert result.memory.index_char_budget > 0
        assert result.memory.type == ""

    @pytest.mark.asyncio
    async def test_load_config_invalid_field_should_raise(self, tmp_path):
        """非法字段值: 应抛出Pydantic ValidationError"""
        yaml_content = "memory:\n  type: invalid_type\n"
        config_file = tmp_path / "agent.yaml"
        config_file.write_text(yaml_content, encoding="utf-8")

        with pytest.raises(Exception):
            await AgentFactory._load_agent_config(str(config_file))


# ==================== _load_agent_class 测试 ====================


class TestLoadAgentClass:
    """测试_load_agent_class静态方法 - 从AGENT_REGISTRY加载实现类"""

    @pytest.mark.asyncio
    async def test_load_class_success_should_return_agent_class(self):
        """正常流程: 应返回Agent实现类"""
        from src.agent.base_agent import BaseAgent

        mock_agent_class = type(
            "MockAgent",
            (BaseAgent,),
            {
                "process_message": staticmethod(lambda self, msg, uid, tid, **kw: "ok"),
                "process_message_stream": staticmethod(
                    lambda self, msg, uid, tid, **kw: []
                ),
                "initialize": staticmethod(lambda self: None),
                "cleanup": staticmethod(lambda self: None),
            },
        )

        mock_module = MagicMock()
        mock_module.MockAgent = mock_agent_class

        with (
            patch(
                "src.agent.factory.get_agent_class_info",
                return_value=("test.module", "MockAgent"),
            ),
            patch("importlib.import_module", return_value=mock_module),
        ):
            result = AgentFactory._load_agent_class("personal-assistant")
            assert result is mock_agent_class

    @pytest.mark.asyncio
    async def test_load_class_unregistered_agent_should_raise(self):
        """未注册的agent_id: 应抛出RuntimeError"""
        with (
            patch(
                "src.agent.factory.get_agent_class_info",
                side_effect=KeyError("未注册的Agent ID"),
            ),
            pytest.raises(RuntimeError, match="未注册"),
        ):
            AgentFactory._load_agent_class("unknown-agent")

    @pytest.mark.asyncio
    async def test_load_class_not_subclass_of_base_agent_should_raise(self):
        """Agent类未继承BaseAgent: 应抛出RuntimeError"""

        class NotAnAgent:
            pass

        mock_module = MagicMock()
        mock_module.NotAnAgent = NotAnAgent

        with (
            patch(
                "src.agent.factory.get_agent_class_info",
                return_value=("test.module", "NotAnAgent"),
            ),
            patch("importlib.import_module", return_value=mock_module),
        ):
            with pytest.raises(RuntimeError, match="必须继承自BaseAgent"):
                AgentFactory._load_agent_class("test-agent")

    @pytest.mark.asyncio
    async def test_load_class_import_error_should_raise_runtime_error(self):
        """模块导入失败: 应抛出RuntimeError"""
        with (
            patch(
                "src.agent.factory.get_agent_class_info",
                return_value=("nonexistent.module", "SomeClass"),
            ),
            patch(
                "importlib.import_module",
                side_effect=ImportError("模块不存在"),
            ),
            pytest.raises(RuntimeError, match="无法导入Agent模块"),
        ):
            AgentFactory._load_agent_class("test-agent")

    @pytest.mark.asyncio
    async def test_load_class_attribute_error_should_raise_runtime_error(self):
        """类不存在: 应抛出RuntimeError"""
        mock_module = MagicMock(spec=[])
        del mock_module.NonExistentClass

        with (
            patch(
                "src.agent.factory.get_agent_class_info",
                return_value=("some.module", "NonExistentClass"),
            ),
            patch("importlib.import_module", return_value=mock_module),
        ):
            with pytest.raises(RuntimeError, match="不存在"):
                AgentFactory._load_agent_class("test-agent")

    @pytest.mark.asyncio
    async def test_load_class_generic_exception_should_raise_runtime_error(self):
        """通用异常: 应被包装为RuntimeError"""
        mock_module = MagicMock()

        type(mock_module).SomeClass = property(
            lambda self: (_ for _ in ()).throw(TypeError("type error"))
        )

        with (
            patch(
                "src.agent.factory.get_agent_class_info",
                return_value=("test.module", "SomeClass"),
            ),
            patch("importlib.import_module", return_value=mock_module),
        ):
            with pytest.raises(RuntimeError, match=r"加载Agent.*失败"):
                AgentFactory._load_agent_class("test-agent")


# ==================== _validate_tool_names 测试 ====================


class TestValidateToolNames:
    """测试_validate_tool_names静态方法"""

    def test_validate_known_tools_should_not_warn(self):
        """已知工具: 不应输出warning"""
        mock_config = Mock()
        mock_config.agent_id = "test-agent"
        mock_config.tools = ["create_todo", "search_memories"]

        mock_tools_config = Mock()
        mock_tools_config.tool_groups = {}
        mock_tools_config.list_enabled_mcp_servers.return_value = []
        mock_tools_config.list_enabled_internal_tools.return_value = [
            Mock(name="create_todo"),
            Mock(name="search_memories"),
        ]
        mock_tools_config.list_enabled_external_tools.return_value = []

        with patch(
            "src.config.tools_config.get_config", return_value=mock_tools_config
        ):
            AgentFactory._validate_tool_names(mock_config)

    def test_validate_unknown_tools_should_warn(self):
        """未知工具: 应输出warning但不阻塞"""
        mock_config = Mock()
        mock_config.agent_id = "test-agent"
        mock_config.tools = ["create_todo", "unknown_tool"]
        mock_config.optional_tools = []

        mock_tools_config = Mock()
        mock_tools_config.tool_groups = {}
        mock_tools_config.list_enabled_mcp_servers.return_value = []
        mock_tools_config.list_enabled_internal_tools.return_value = [
            Mock(name="create_todo"),
        ]
        mock_tools_config.list_enabled_external_tools.return_value = []

        with (
            patch("src.config.tools_config.get_config", return_value=mock_tools_config),
            patch("src.agent.factory.logger") as mock_logger,
        ):
            AgentFactory._validate_tool_names(mock_config)
            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "unknown_tool" in warning_msg

    def test_validate_expert_tools_should_be_known(self):
        """Expert工具(web_research, geo_research): 应被识别为已知工具"""
        mock_config = Mock()
        mock_config.agent_id = "test-agent"
        mock_config.tools = ["web_research", "geo_navigator"]

        mock_tools_config = Mock()
        mock_tools_config.tool_groups = {}
        mock_tools_config.list_enabled_mcp_servers.return_value = []
        mock_tools_config.list_enabled_internal_tools.return_value = []
        mock_tools_config.list_enabled_external_tools.return_value = []

        with (
            patch("src.config.tools_config.get_config", return_value=mock_tools_config),
            patch("src.agent.factory.logger") as mock_logger,
        ):
            AgentFactory._validate_tool_names(mock_config)
            mock_logger.warning.assert_not_called()

    def test_validate_generate_image_internal_tool_should_be_known(self):
        """generate_image作为内部工具时应被识别为已知工具"""
        mock_config = Mock()
        mock_config.agent_id = "test-agent"
        mock_config.tools = ["generate_image"]
        mock_config.optional_tools = []

        mock_tools_config = Mock()
        mock_tools_config.tool_groups = {}
        mock_tools_config.list_enabled_mcp_servers.return_value = []
        mock_tool = Mock()
        mock_tool.name = "generate_image"
        mock_tools_config.list_enabled_internal_tools.return_value = [mock_tool]
        mock_tools_config.list_enabled_external_tools.return_value = []

        with (
            patch("src.config.tools_config.get_config", return_value=mock_tools_config),
            patch("src.agent.factory.logger") as mock_logger,
        ):
            AgentFactory._validate_tool_names(mock_config)
            mock_logger.warning.assert_not_called()

    def test_validate_python_executor_external_tool_should_be_known(self):
        """python_executor作为外部工具时应被识别为已知工具"""
        mock_config = Mock()
        mock_config.agent_id = "test-agent"
        mock_config.tools = []
        mock_config.optional_tools = ["python_executor"]

        mock_tools_config = Mock()
        mock_tools_config.tool_groups = {}
        mock_tools_config.list_enabled_mcp_servers.return_value = []
        mock_tool = Mock()
        mock_tool.name = "python_executor"
        mock_tools_config.list_enabled_internal_tools.return_value = []
        mock_tools_config.list_enabled_external_tools.return_value = [mock_tool]

        with (
            patch("src.config.tools_config.get_config", return_value=mock_tools_config),
            patch("src.agent.factory.logger") as mock_logger,
        ):
            AgentFactory._validate_tool_names(mock_config)
            mock_logger.warning.assert_not_called()

    def test_validate_mcp_tools_should_be_known(self):
        """MCP工具: 应从server配置中识别"""
        mock_config = Mock()
        mock_config.agent_id = "test-agent"
        mock_config.tools = ["mcp_search"]

        mock_server = Mock()
        mock_server.tool_names = {"search": "mcp_search"}

        mock_tools_config = Mock()
        mock_tools_config.tool_groups = {}
        mock_tools_config.list_enabled_mcp_servers.return_value = [mock_server]
        mock_tools_config.list_enabled_internal_tools.return_value = []
        mock_tools_config.list_enabled_external_tools.return_value = []

        with (
            patch("src.config.tools_config.get_config", return_value=mock_tools_config),
            patch("src.agent.factory.logger") as mock_logger,
        ):
            AgentFactory._validate_tool_names(mock_config)
            mock_logger.warning.assert_not_called()

    def test_validate_tool_group_names_should_be_known(self):
        """工具组名(如 todo_manager_group)应被识别为已知工具, 不告警"""
        mock_config = Mock()
        mock_config.agent_id = "test-agent"
        mock_config.tools = ["todo_manager_group"]
        mock_config.optional_tools = []

        group_cfg = Mock()
        group_cfg.name = "todo_manager_group"

        mock_tools_config = Mock()
        mock_tools_config.tool_groups = {"todo_manager_group": group_cfg}
        mock_tools_config.list_enabled_mcp_servers.return_value = []
        mock_tools_config.list_enabled_internal_tools.return_value = []
        mock_tools_config.list_enabled_external_tools.return_value = []

        with (
            patch("src.config.tools_config.get_config", return_value=mock_tools_config),
            patch("src.agent.factory.logger") as mock_logger,
        ):
            AgentFactory._validate_tool_names(mock_config)
            mock_logger.warning.assert_not_called()

    def test_validate_exception_should_not_block(self):
        """工具配置加载异常: 不应阻塞启动"""
        mock_config = Mock()
        mock_config.agent_id = "test-agent"
        mock_config.tools = ["tool1"]

        with patch(
            "src.config.tools_config.get_config",
            side_effect=Exception("配置加载失败"),
        ):
            AgentFactory._validate_tool_names(mock_config)


# ==================== load_agent_config(轻量级) 测试 ====================


class TestLoadAgentConfigLightweight:
    """测试load_agent_config轻量级加载方法"""

    @pytest.fixture
    def factory(self, tmp_path):
        """创建使用临时目录的factory"""
        return AgentFactory(implementations_dir=str(tmp_path))

    @pytest.mark.asyncio
    async def test_load_agent_config_success_should_return_config(
        self, factory, tmp_path
    ):
        """正常流程: 应返回AgentConfig"""
        config_dir = tmp_path / "personal_assistant"
        config_dir.mkdir()
        (config_dir / "agent.yaml").write_text(
            "agent_id: personal-assistant\nname: Test\n", encoding="utf-8"
        )

        with patch(
            "src.agent.factory.get_agent_directory",
            return_value="personal_assistant",
        ):
            result = await factory.load_agent_config("personal-assistant")

            from src.config.agent_config import AgentConfig

            assert isinstance(result, AgentConfig)
            assert result.name == "Test"

    @pytest.mark.asyncio
    async def test_load_agent_config_not_found_should_raise(self, factory):
        """配置文件不存在: 应抛出FileNotFoundError"""
        with patch("src.agent.factory.get_agent_directory", return_value="nonexistent"):
            with pytest.raises(FileNotFoundError, match="Agent配置文件不存在"):
                await factory.load_agent_config("nonexistent-agent")

    @pytest.mark.asyncio
    async def test_load_agent_config_model_misuse_should_raise_with_hint(self, factory):
        """模型ID误用: 应抛出包含提示的错误"""
        with patch("src.agent.factory.get_agent_directory", return_value="gpt_4"):
            with patch.object(
                factory, "get_supported_agents", return_value=["personal-assistant"]
            ):
                with pytest.raises(FileNotFoundError, match="模型名称"):
                    await factory.load_agent_config("gpt-4")

    @pytest.mark.asyncio
    async def test_load_agent_config_local_prefix_misuse_should_raise(self, factory):
        """local:前缀误用: 应抛出包含提示的错误"""
        with patch("src.agent.factory.get_agent_directory", return_value="local_x"):
            with patch.object(
                factory, "get_supported_agents", return_value=["personal-assistant"]
            ):
                with pytest.raises(FileNotFoundError, match="模型名称"):
                    await factory.load_agent_config("some:local:model")

    @pytest.mark.asyncio
    async def test_load_agent_config_validation_failure_should_raise(
        self, factory, tmp_path
    ):
        """配置校验失败: 应向上抛出异常"""
        config_dir = tmp_path / "personal_assistant"
        config_dir.mkdir()
        (config_dir / "agent.yaml").write_text("model_id: ''", encoding="utf-8")

        with (
            patch(
                "src.agent.factory.get_agent_directory",
                return_value="personal_assistant",
            ),
            pytest.raises(Exception, match="model_id"),
        ):
            await factory.load_agent_config("personal-assistant")


# ==================== get_supported_agents 测试 ====================


class TestGetSupportedAgents:
    """测试get_supported_agents方法"""

    def test_get_supported_agents_discovery_success(self):
        """发现机制成功: 应返回Agent列表"""
        factory = AgentFactory()

        with patch(
            "src.agent.factory.get_available_agents",
            return_value=["personal-assistant", "health-assistant"],
        ):
            result = factory.get_supported_agents()

            assert result == ["personal-assistant", "health-assistant"]

    def test_get_supported_agents_fallback_to_manual_scan(self, tmp_path):
        """发现机制失败: 应回退到手动扫描"""
        factory = AgentFactory(implementations_dir=str(tmp_path))

        import yaml

        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text(
            yaml.dump({"agent_id": "test-agent"}), encoding="utf-8"
        )

        with patch(
            "src.agent.factory.get_available_agents",
            side_effect=Exception("发现失败"),
        ):
            result = factory.get_supported_agents()

            assert "test-agent" in result

    def test_get_supported_agents_returns_list(self, tmp_path):
        """返回类型: 应返回list[str]"""
        factory = AgentFactory(implementations_dir=str(tmp_path))

        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        import yaml

        (agent_dir / "agent.yaml").write_text(
            yaml.dump({"agent_id": "test-agent"}), encoding="utf-8"
        )

        result = factory._manual_scan_agents()
        assert isinstance(result, list)
        assert "test-agent" in result


# ==================== _manual_scan_agents 测试 ====================


class TestManualScanAgents:
    """测试_manual_scan_agents备用方法"""

    def test_manual_scan_dir_not_exists_should_return_empty(self):
        """目录不存在: 应返回空列表"""
        factory = AgentFactory(implementations_dir="/nonexistent/path")

        result = factory._manual_scan_agents()

        assert result == []

    def test_manual_scan_valid_agents_should_return_ids(self, tmp_path):
        """有效Agent目录: 应返回Agent ID列表"""
        factory = AgentFactory(implementations_dir=str(tmp_path))

        import yaml

        for name, agent_id in [("agent_a", "agent-a"), ("agent_b", "agent-b")]:
            agent_dir = tmp_path / name
            agent_dir.mkdir()
            (agent_dir / "agent.yaml").write_text(
                yaml.dump({"agent_id": agent_id}), encoding="utf-8"
            )

        result = factory._manual_scan_agents()

        assert "agent-a" in result
        assert "agent-b" in result
        assert len(result) == 2

    def test_manual_scan_no_agent_yaml_should_skip_dir(self, tmp_path):
        """缺少agent.yaml: 应跳过该目录"""
        factory = AgentFactory(implementations_dir=str(tmp_path))

        (tmp_path / "not_an_agent").mkdir()

        result = factory._manual_scan_agents()

        assert result == []

    def test_manual_scan_non_dir_should_skip(self, tmp_path):
        """非目录文件: 应跳过"""
        factory = AgentFactory(implementations_dir=str(tmp_path))

        (tmp_path / "readme.md").write_text("not a dir", encoding="utf-8")

        result = factory._manual_scan_agents()

        assert result == []

    def test_manual_scan_invalid_yaml_should_use_dir_name(self, tmp_path):
        """无效YAML: 应使用目录名作为agent_id"""
        factory = AgentFactory(implementations_dir=str(tmp_path))

        agent_dir = tmp_path / "broken_agent"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text("", encoding="utf-8")

        result = factory._manual_scan_agents()

        assert len(result) == 1
        assert "broken_agent" in result[0]

    def test_manual_scan_corrupted_yaml_should_skip(self, tmp_path):
        """损坏的YAML: 应跳过并记录warning"""
        factory = AgentFactory(implementations_dir=str(tmp_path))

        agent_dir = tmp_path / "corrupted"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text(":\n  invalid: [\n", encoding="utf-8")

        result = factory._manual_scan_agents()

        assert isinstance(result, list)
