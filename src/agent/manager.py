"""Agent业务管理器.

提供API友好的Agent接口,专注于业务适配而非具体创建逻辑.
基于v1.5极简化架构理念,只提供必要的接口,避免过度设计.

职责:
- API层的Agent业务适配
- 统一的错误处理和异常转换
- Agent元数据的格式化和展示
- 全局AgentFactory实例管理

设计原则:
- 极简但有效
- 职责清晰分离
- 组合优于继承
- 错误友好
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.agent.factory import AgentFactory

if TYPE_CHECKING:
    from src.agent.base_agent import BaseAgent
    from src.config.agent_config import AgentConfig

logger = logging.getLogger(__name__)


class AgentManager:
    """Agent业务管理器.

    提供API友好的Agent接口,作为API层和AgentFactory之间的适配层.
    专注于业务逻辑适配,不处理具体的Agent创建技术细节.

    Examples:
        >>> manager = AgentManager()
        >>> agent = await manager.get_agent("personal-assistant")
        >>> agents = await manager.list_agents()

    """

    def __init__(self) -> None:
        """初始化Agent管理器."""
        self._factory = AgentFactory()
        self._config_cache: dict[str, AgentConfig] = {}
        logger.debug("🏭 AgentManager初始化完成")

    async def get_agent(self, agent_id: str) -> BaseAgent:
        """获取Agent实例.

        提供API友好的Agent获取接口,包含统一的错误处理和日志记录.
        内部委托给AgentFactory进行实际的Agent创建.

        Args:
            agent_id: Agent ID

        Returns:
            Agent实例

        Raises:
            ValueError: 当Agent ID不支持时
            RuntimeError: 当Agent创建失败时

        """
        logger.debug("🔄 获取Agent实例: %s", agent_id)
        try:
            agent = await self._factory.create_agent(agent_id)
            logger.debug("✅ Agent获取成功: %s", agent_id)
            return agent
        except Exception as e:
            logger.error("❌ Agent获取失败: %s, 错误: %s", agent_id, e)
            raise RuntimeError(f"Agent获取失败: {agent_id}") from e

    async def list_agents(self) -> list[dict[str, str]]:
        """获取所有可用的Agent列表.

        提供API友好的Agent列表接口,返回标准化的Agent元数据.
        使用轻量级配置加载,避免创建完整的Agent实例.

        Returns:
            Agent列表,每个Agent包含id,name,description字段

        """
        logger.debug("🔄 获取Agent列表")
        try:
            supported_agents = self._factory.get_supported_agents()
            agents = []

            for agent_id in supported_agents:
                try:
                    # 使用轻量级配置加载,避免创建Agent实例
                    if agent_id in self._config_cache:
                        config = self._config_cache[agent_id]
                        logger.debug("📦 使用缓存的配置: %s", agent_id)
                    else:
                        config = await self._factory.load_agent_config(agent_id)
                        self._config_cache[agent_id] = config
                        logger.debug("📄 轻量级加载配置: %s", agent_id)

                    agents.append({
                        "id": config.agent_id,
                        "name": config.name,
                        "description": config.description,
                    })
                    logger.debug(f"✅ 加载Agent配置: {config.agent_id}")

                except Exception as e:
                    logger.error("❌ 配置加载失败 %s: %s", agent_id, e)
                    # 调试友好:明确报告错误,不静默fallback
                    raise RuntimeError(f"Agent配置加载失败: {agent_id}") from e

            logger.debug(f"✅ 获取到 {len(agents)} 个Agent")
            return agents

        except Exception as e:
            logger.error("❌ 获取Agent列表失败: %s", e)
            raise


# 全局Agent管理器实例
_global_manager: AgentManager | None = None


def get_agent_manager() -> AgentManager:
    """获取全局Agent管理器实例.

    Returns:
        Agent管理器实例

    """
    global _global_manager
    if _global_manager is None:
        _global_manager = AgentManager()
        logger.info("🏭 创建全局Agent管理器实例")
    return _global_manager


def set_agent_manager(manager: AgentManager) -> None:
    """设置全局Agent管理器实例.

    Args:
        manager: Agent管理器实例

    """
    global _global_manager
    _global_manager = manager
    logger.info(f"🔧 设置全局Agent管理器: {manager.__class__.__name__}")


# 便捷函数
async def get_agent(agent_id: str) -> BaseAgent:
    """便捷函数:获取Agent实例.

    Args:
        agent_id: Agent ID

    Returns:
        Agent实例

    """
    manager = get_agent_manager()
    return await manager.get_agent(agent_id)


async def list_agents() -> list[dict[str, str]]:
    """便捷函数:获取Agent列表.

    Returns:
        Agent列表

    """
    manager = get_agent_manager()
    return await manager.list_agents()


# 导出主要接口
__all__ = [
    "AgentManager",
    "get_agent",
    "get_agent_manager",
    "list_agents",
    "set_agent_manager",
]
