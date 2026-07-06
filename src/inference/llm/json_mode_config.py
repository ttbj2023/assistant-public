"""JSON 模式配置工具.

提供统一的模型 JSON 模式配置获取, 支持三段式回退:
model_registry 元数据 -> local: format -> json_object 默认.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_json_mode_config(
    model_id: str,
    log_level: int = logging.WARNING,
) -> dict[str, Any]:
    """获取模型的 JSON 模式配置.

    三段式回退: model_registry 元数据 -> local: format -> json_object 默认.

    Args:
        model_id: 目标模型 ID
        log_level: 元数据获取失败时的日志级别

    Returns:
        可直接传入 llm.ainvoke() 的 JSON 配置字典

    """
    try:
        from src.inference.llm.definitions.model_registry import get_model

        model_metadata = get_model(model_id)
        if model_metadata:
            return model_metadata.get_json_mode_config()
    except Exception as e:
        logger.log(log_level, "获取模型元数据失败: %s", e)

    if model_id.startswith("local:"):
        return {"format": "json"}

    return {"response_format": {"type": "json_object"}}
