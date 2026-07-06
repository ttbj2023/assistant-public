"""模型元数据轻量检查.

从 src/api/utils/lightweight_health_checker.py 下沉至 inference 层,
消除 agent -> api 的反向依赖. 仅检查 model_registry 注册情况, 不加载模型实例.
"""

from __future__ import annotations

import logging
from typing import Any

from src.inference.llm.definitions.model_registry import get_model

logger = logging.getLogger(__name__)


def check_model_metadata(model_id: str) -> dict[str, Any]:
    """检查模型元数据是否注册.

    Args:
        model_id: 模型ID,格式为 "provider:model_name"

    Returns:
        模型元数据检查结果

    """
    try:
        metadata = get_model(model_id)

        if metadata:
            return {
                "available": True,
                "model_id": model_id,
                "model_type": metadata.model_type.value,
                "provider": metadata.provider,
                "capabilities": [cap.value for cap in metadata.capabilities],
                "name": metadata.name,
                "description": metadata.description,
            }
        return {
            "available": False,
            "model_id": model_id,
            "error": "模型未在 builtin_models 中注册",
        }

    except Exception as e:
        logger.debug("模型可用性检查失败(%s): %s", model_id, e)
        return {
            "available": False,
            "model_id": model_id,
            "error": f"模型元数据检查失败: {e!s}",
        }


__all__ = ["check_model_metadata"]
