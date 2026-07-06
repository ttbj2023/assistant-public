"""Agent具体实现模块."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)  # noqa: RUF067


def get_available_agents() -> list[str]:  # noqa: RUF067
    """获取可用的Agent列表."""
    implementations_dir = Path(__file__).parent
    agents: list[str] = []

    for item in implementations_dir.iterdir():
        if item.is_dir() and (item / "agent.yaml").exists():
            try:
                config_path = item / "agent.yaml"
                with Path(config_path).open(encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    agent_id = config.get("agent_id", item.name)
                    agents.append(agent_id)
            except Exception as e:
                logger.warning(f"无法读取Agent配置 {item.name}: {e}")

    return agents


def get_agent_directory(agent_id: str) -> str:  # noqa: RUF067
    """获取Agent的目录名."""
    return agent_id.replace("-", "_")


__all__ = [
    "get_agent_directory",
    "get_available_agents",
]
