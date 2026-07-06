"""模型注册表和查询接口.

提供独立的模型查询和管理功能. 使用 dict 索引实现 O(1) 查找.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .builtin_data import (
    create_aliyun_token_plan_models,
    create_ark_agent_plan_models,
    create_builtin_models,
    create_scnet_models,
)

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .metadata import ModelMetadata

# 全局缓存
_BUILTIN_MODELS_CACHE: list[ModelMetadata] | None = None
_MODEL_INDEX: dict[str, ModelMetadata] | None = None


def _build_caches() -> tuple[list[ModelMetadata], dict[str, ModelMetadata]]:
    """构建并缓存模型列表和索引."""
    global _BUILTIN_MODELS_CACHE, _MODEL_INDEX
    if _BUILTIN_MODELS_CACHE is None:
        models = (
            create_builtin_models()
            + create_ark_agent_plan_models()
            + create_aliyun_token_plan_models()
            + create_scnet_models()
        )
        _BUILTIN_MODELS_CACHE = models
        _MODEL_INDEX = {m.id: m for m in models}
    if _MODEL_INDEX is None:
        _MODEL_INDEX = {m.id: m for m in _BUILTIN_MODELS_CACHE}
    return _BUILTIN_MODELS_CACHE, _MODEL_INDEX


def get_model(model_id: str) -> ModelMetadata | None:
    """根据模型 ID 获取模型元数据 (O(1) dict 索引).

    Args:
        model_id: 模型 ID, 格式为 "provider:model_name"

    Returns:
        找到的模型元数据, 如果不存在则返回 None

    """
    _, index = _build_caches()
    return index.get(model_id)


def list_models() -> list[str]:
    """列出所有可用的模型 ID.

    Returns:
        模型 ID 列表

    """
    models, _ = _build_caches()
    return [model.id for model in models]


def get_models_by_provider(provider: str) -> list[ModelMetadata]:
    """根据提供者获取模型列表.

    Args:
        provider: 提供者名称

    Returns:
        该提供者的所有模型

    """
    models, _ = _build_caches()
    return [model for model in models if model.provider == provider]


def register_custom_model(model: ModelMetadata) -> None:
    """注册自定义模型到缓存中.

    Args:
        model: 模型元数据对象

    """
    models, index = _build_caches()

    if model.id in index:
        existing = index[model.id]
        idx = models.index(existing)
        models[idx] = model
    else:
        models.append(model)

    index[model.id] = model


def clear_model_cache() -> None:
    """清空模型缓存."""
    global _BUILTIN_MODELS_CACHE, _MODEL_INDEX
    _BUILTIN_MODELS_CACHE = None
    _MODEL_INDEX = None
