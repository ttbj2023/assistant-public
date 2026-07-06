"""Agent工厂模式实现.

提供统一的Agent创建接口, 支持调试装饰器的可选集成.

配置加载流程:
  agent.yaml → AgentConfig(yaml_dict)
  缺少的字段由Pydantic Field defaults自动兜底

Agent实现类路由:
  AGENT_REGISTRY(agent_id → module_path, class_name)
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from pathlib import Path

import yaml

from src.config.agent_config import AgentConfig

from .agents_implementations import get_agent_directory, get_available_agents
from .agents_implementations.agent_registry import get_agent_class_info
from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class AgentFactory:
    """Agent工厂实现."""

    def __init__(
        self,
        implementations_dir: str = "src/agent/agents_implementations",
    ) -> None:
        self.implementations_dir = Path(implementations_dir)

    async def create_agent(self, agent_id: str) -> BaseAgent:
        """创建Agent实例.

        Args:
            agent_id: Agent ID

        Returns:
            Agent实例

        Raises:
            FileNotFoundError: Agent配置文件不存在
            ImportError: Agent类加载失败

        """
        logger.debug("🏭 创建Agent: %s", agent_id)

        config_path = self._resolve_config_path(agent_id)

        agent_config = await AgentFactory._load_agent_config(str(config_path))
        logger.debug("✅ AgentConfig加载成功: %s", agent_id)

        AgentFactory._validate_tool_names(agent_config)

        agent_class = AgentFactory._load_agent_class(agent_id)

        agent = agent_class(agent_config)
        logger.debug("✅ 创建Agent实例: %s", agent_id)

        try:
            await agent.initialize()
            logger.debug("✅ Agent初始化完成: %s", agent_id)
        except Exception as e:
            logger.error("❌ Agent初始化失败 %s: %s", agent_id, e)
            raise

        logger.info("✅ Agent创建成功: %s", agent_id)
        return agent

    def _resolve_config_path(self, agent_id: str) -> Path:
        """查找agent.yaml文件, 不存在则抛出详细错误.

        Args:
            agent_id: Agent ID

        Returns:
            agent.yaml文件路径

        Raises:
            FileNotFoundError: 配置文件不存在

        """
        dir_name = get_agent_directory(agent_id)
        config_path = self.implementations_dir / dir_name / "agent.yaml"

        if config_path.exists():
            return config_path

        if agent_id and (
            ":" in agent_id
            or agent_id.startswith((
                "gpt-",
                "claude-",
                "gemini-",
                "deepseek-",
                "zhipu-",
            ))
            or "local:" in agent_id
        ):
            available_agents = self.get_supported_agents()
            examples = (available_agents or [])[:3]

            error_msg = [
                f"❌ 检测到您使用了模型名称 '{agent_id}' 作为 agent ID.",
                "",
                "🔍 **Agent ID 与 Model ID 的区别**:",
                "• **Agent ID**: 用于标识对话助手类型, 如 'personal_assistant'",
                "• **Model ID**: 用于标识LLM模型, 如 'local:qwen3.5:9b'",
                "",
                "💡 **正确的API使用方式**:",
                "• 聊天接口: POST /v1/chat/completions - 使用 agent_id来填写model 参数",
                "• 模型列表: GET /v1/models - 查看可用agent列表(兼容前端)",
                "",
            ]

            if examples:
                error_msg.extend([
                    "📋 **可用的Agent ID**:",
                    f"   • {', '.join(examples)}{'...' if len(available_agents) > 3 else ''}",
                    "",
                ])

            error_msg.extend([
                "🚀 **快速开始**:",
                "   curl -X POST http://localhost:8000/v1/chat/completions \\",
                '     -H "Authorization: Bearer YOUR_API_KEY" \\',
                '     -H "Content-Type: application/json" \\',
                '     -d \'{"model": "personal_assistant", "messages": [{"role": "user", "content": "你好"}]}\'',
                "",
                f"🔍 配置文件查找详情: {config_path}",
            ])

            raise FileNotFoundError("\n".join(error_msg))

        raise FileNotFoundError(f"Agent配置文件不存在: {config_path}")

    @staticmethod
    def _validate_tool_names(agent_config: AgentConfig) -> None:
        """验证Agent配置中的工具名称是否可解析, 仅warning不阻塞."""
        try:
            from src.config.tools_config import get_config as get_tools_config
            from src.tools.experts import EXPERT_TOOL_NAMES

            tools_config = get_tools_config()

            all_mcp_names: set[str] = set()
            for server in tools_config.list_enabled_mcp_servers():
                all_mcp_names.update(server.tool_names.values())

            internal_names = {
                t.name for t in tools_config.list_enabled_internal_tools()
            }

            external_names = {
                t.name for t in tools_config.list_enabled_external_tools()
            }

            # 工具组名也算已知: agent.yaml 可声明组名 (如 todo_manager_group),
            # 由 inference_coordinator._expand_group_names 展开为成员工具
            group_names = {g.name for g in tools_config.tool_groups.values()}

            known_tools = (
                internal_names
                | EXPERT_TOOL_NAMES
                | all_mcp_names
                | external_names
                | group_names
            )

            unknown = [
                t
                for t in agent_config.tools + agent_config.optional_tools
                if t not in known_tools
            ]
            if unknown:
                logger.warning(
                    f"⚠️ Agent '{agent_config.agent_id}' 配置了未知工具: {unknown}, "
                    f"已知工具: internal={sorted(internal_names)}, "
                    f"expert={sorted(EXPERT_TOOL_NAMES)}, "
                    f"mcp={sorted(all_mcp_names)}",
                )
        except Exception as e:
            logger.debug("工具名称验证跳过(非阻塞): %s", e)

    @staticmethod
    async def _load_agent_config(config_path: str) -> AgentConfig:
        """从agent.yaml加载AgentConfig.

        缺少的字段由Pydantic Field defaults自动兜底.
        yaml中的嵌套dict(如memory)会被Pydantic自动解析为对应的子模型.

        Args:
            config_path: agent.yaml文件路径

        Returns:
            AgentConfig实例

        Raises:
            ValidationError: yaml内容未通过Pydantic校验

        """
        yaml_text = await asyncio.to_thread(
            Path(config_path).read_text, encoding="utf-8"
        )
        yaml_config = yaml.safe_load(yaml_text) or {}

        return AgentConfig(**yaml_config)

    @staticmethod
    def _load_agent_class(agent_id: str) -> type:
        """从AGENT_REGISTRY加载Agent实现类.

        Args:
            agent_id: Agent ID

        Returns:
            Agent实现类

        Raises:
            RuntimeError: agent_id未注册,模块导入失败,类不存在或未继承BaseAgent

        """
        try:
            module_path, class_name = get_agent_class_info(agent_id)
        except KeyError as e:
            raise RuntimeError(str(e)) from e

        try:
            logger.debug(
                "🔧 加载Agent类: %s -> %s.%s",
                agent_id,
                module_path,
                class_name,
            )

            module = importlib.import_module(module_path, __package__)
            agent_class = getattr(module, class_name)

            if not issubclass(agent_class, BaseAgent):
                raise ValueError(f"Agent类 '{class_name}' 必须继承自BaseAgent")

            logger.debug("✅ Agent类加载成功: %s -> %s", agent_id, class_name)
            return agent_class

        except ImportError as e:
            raise RuntimeError(
                f"无法导入Agent模块 '{module_path}' (Agent: {agent_id}): {e}",
            ) from e
        except AttributeError as e:
            raise RuntimeError(
                f"Agent类 '{class_name}' 在模块 '{module_path}' 中不存在 (Agent: {agent_id}): {e}",
            ) from e
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"加载Agent '{agent_id}' 失败: {e}") from e

    async def load_agent_config(self, agent_id: str) -> AgentConfig:
        """轻量级加载Agent配置,不创建实例.

        适用于list_agents()等只需要元数据的场景.

        Args:
            agent_id: Agent ID

        Returns:
            AgentConfig: 配置对象

        Raises:
            FileNotFoundError: Agent配置文件不存在
            ValidationError: 配置验证失败

        """
        logger.debug("📄 轻量级加载Agent配置: %s", agent_id)

        config_path = self._resolve_config_path(agent_id)

        try:
            return await AgentFactory._load_agent_config(str(config_path))
        except Exception as e:
            logger.error("❌ 配置加载失败: %s, 错误: %s", agent_id, e)
            raise

    def get_supported_agents(self) -> list[str]:
        """获取支持的Agent列表."""
        try:
            agents = get_available_agents()
            logger.debug(f"✅ 通过发现机制找到 {len(agents)} 个Agent: {agents}")
            return agents
        except Exception as e:
            logger.warning("⚠️ 使用发现机制失败, 回退到手动扫描: %s", e)
            return self._manual_scan_agents()

    def _manual_scan_agents(self) -> list[str]:
        """手动扫描Agent实现(备用方法)."""
        agents: list[str] = []
        if not self.implementations_dir.exists():
            return agents

        for item in self.implementations_dir.iterdir():
            if item.is_dir() and (item / "agent.yaml").exists():
                try:
                    config_path = item / "agent.yaml"
                    with Path(config_path).open(encoding="utf-8") as f:
                        config_dict = yaml.safe_load(f) or {}

                    agent_id = config_dict.get("agent_id", f"unknown-{item.name}")
                    agents.append(agent_id)
                    logger.debug(
                        f"✅ 手动发现Agent配置: {agent_id} (目录: {item.name})",
                    )
                except Exception as e:
                    logger.warning(f"⚠️ 配置加载失败 {item.name}: {e}")

        return agents


# 便捷函数
