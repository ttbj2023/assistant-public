"""Agent注册表 - Agent ID到实现类的路由映射.

新增Agent时需要在此注册:
1. 在 src/agent/agents_implementations/ 下创建目录和实现
2. 在此文件的 AGENT_REGISTRY 中添加注册项
3. 创建 agent.yaml 配置文件
"""

from __future__ import annotations

AGENT_REGISTRY: dict[str, tuple[str, str]] = {
    "personal-assistant": (
        "src.agent.agents_implementations.personal_assistant.main",
        "PersonalAssistantAgent",
    ),
    "health-assistant": (
        "src.agent.agents_implementations.health_assistant.main",
        "HealthAssistantAgent",
    ),
    "thought-assistant": (
        "src.agent.agents_implementations.thought_assistant.main",
        "ThoughtAssistantAgent",
    ),
}


def get_agent_class_info(
    agent_id: str,
) -> tuple[str, str]:
    """根据agent_id获取实现类的模块路径和类名.

    Args:
        agent_id: Agent ID

    Returns:
        (module_path, class_name) 元组

    Raises:
        KeyError: agent_id未注册

    """
    if agent_id not in AGENT_REGISTRY:
        raise KeyError(
            f"未注册的Agent ID: {agent_id}, 已注册: {list(AGENT_REGISTRY.keys())}",
        )
    return AGENT_REGISTRY[agent_id]


__all__ = [
    "AGENT_REGISTRY",
    "get_agent_class_info",
]
