"""MCP响应格式化器注册表.

管理格式化器名称到类实例的映射. 当前无内置格式化器,
各MCP工具按需在 config.yaml 配置 response_formatters 时
通过 register() 显式注册.
"""

from __future__ import annotations

import logging

from .base import BaseMcpResponseFormatter

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[BaseMcpResponseFormatter]] = {}


def register(name: str, formatter_cls: type[BaseMcpResponseFormatter]) -> None:
    """注册格式化器.

    Args:
        name: 格式化器名称(与 config.yaml 中的 response_formatters 值对应)
        formatter_cls: 格式化器类

    """
    _REGISTRY[name] = formatter_cls
    logger.debug(f"注册MCP响应格式化器: {name} → {formatter_cls.__name__}")


def get_formatter(name: str) -> BaseMcpResponseFormatter | None:
    """按名称获取格式化器实例.

    Args:
        name: 格式化器名称

    Returns:
        格式化器实例, 未找到返回None

    """
    cls = _REGISTRY.get(name)
    if cls is None:
        return None
    return cls()


def list_formatters() -> dict[str, str]:
    """列出所有已注册的格式化器.

    Returns:
        {名称: 类名} 的映射字典

    """
    return {name: cls.__name__ for name, cls in _REGISTRY.items()}
