"""LLM模型管理模块."""

from __future__ import annotations

from .builtin_data import create_builtin_models
from .metadata import ModelMetadata, ModelPricing
from .model_registry import (
    clear_model_cache,
    get_model,
    get_models_by_provider,
    list_models,
    register_custom_model,
)
from .model_types import MODEL_TYPE_DESCRIPTIONS, ModelCapability, ModelType
from .provider_registry import (
    ProviderConfig,
    get_provider_config,
    is_provider_supported,
    list_providers,
    register_provider,
)
from .shared_models import SHARED_MODELS, SharedModel, bind_shared
from .validation import (
    validate_capabilities_consistency,
    validate_model_metadata_basic,
    validate_provider_config,
)

__all__ = [
    "MODEL_TYPE_DESCRIPTIONS",
    "SHARED_MODELS",
    "ModelCapability",
    "ModelMetadata",
    "ModelPricing",
    "ModelType",
    "ProviderConfig",
    "SharedModel",
    "bind_shared",
    "clear_model_cache",
    "create_builtin_models",
    "get_model",
    "get_models_by_provider",
    "get_provider_config",
    "is_provider_supported",
    "list_models",
    "list_providers",
    "register_custom_model",
    "register_provider",
    "validate_capabilities_consistency",
    "validate_model_metadata_basic",
    "validate_provider_config",
]
