"""模型元数据验证逻辑模块.

提供独立的供应商与模型元数据验证函数.
"""

from __future__ import annotations

from typing import Any

from .model_types import ModelCapability
from .provider_registry import get_provider_config


def validate_provider_config(provider_name: str) -> None:
    """验证供应商配置是否存在且有效."""
    try:
        provider_config = get_provider_config(provider_name)

        if not provider_config.name:
            raise ValueError(f"供应商配置无效: {provider_name}")

    except ValueError as e:
        raise ValueError(f"不支持的供应商: {provider_name}. 错误: {e}") from None
    except Exception as e:
        raise ValueError(f"供应商验证失败: {provider_name}. 错误: {e}") from None


def validate_model_metadata_basic(metadata: Any) -> None:
    """验证模型元数据的基本完整性."""
    validate_capabilities_consistency(metadata.capabilities)


def validate_capabilities_consistency(
    capabilities: list[ModelCapability],
) -> None:
    """验证能力之间的一致性."""
    capabilities_set = set(capabilities)

    if (
        ModelCapability.TOOL_CALLING in capabilities_set
        and ModelCapability.REASONING not in capabilities_set
    ):
        raise ValueError("工具调用能力需要推理能力")
