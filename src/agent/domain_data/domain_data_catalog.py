"""领域数据注册表 - 名称到实现类的映射.

类似 tool_catalog, 用 class_path 延迟导入, 避免框架层对具体实现的硬依赖.
新增领域数据时, 只需在此注册 class_path.
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

_DOMAIN_DATA_REGISTRY: dict[str, dict[str, str]] = {
    "health": {
        "class_path": (
            "src.agent.agents_implementations.health_assistant."
            "health_domain_data.HealthDomainData"
        ),
    },
    "user_profile": {
        "class_path": (
            "src.agent.domain_data.user_profile_domain_data.UserProfileDomainData"
        ),
    },
    "insights": {
        "class_path": ("src.agent.domain_data.insights_domain_data.InsightsDomainData"),
    },
}


def get_domain_data_class(name: str) -> type:
    """根据名称获取 domain_data 实现类.

    Args:
        name: domain_data 名称 (如 "health")

    Returns:
        domain_data 实现类 (BaseDomainData 的子类)

    Raises:
        KeyError: 名称未注册
        ImportError: 模块导入失败

    """
    entry = _DOMAIN_DATA_REGISTRY.get(name)
    if not entry:
        raise KeyError(
            f"未注册的领域数据: '{name}', 可用: {list(_DOMAIN_DATA_REGISTRY)}",
        )

    class_path = entry["class_path"]
    module_path, class_name = class_path.rsplit(".", 1)

    try:
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
    except (ImportError, AttributeError) as e:
        raise ImportError(f"加载领域数据 '{name}' 失败: {e}") from e

    return cls


def get_available_domain_data() -> list[str]:
    """获取所有已注册的 domain_data 名称."""
    return list(_DOMAIN_DATA_REGISTRY)


__all__ = ["get_available_domain_data", "get_domain_data_class"]
