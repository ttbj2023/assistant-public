"""LLM定义模块单元测试.

覆盖 inference/llm/definitions/ 的核心逻辑:
- model_types: ModelType, ModelCapability 枚举
- provider_registry: ProviderConfig, get_provider_config, register_provider
- model_registry: get_model, list_models, register_custom_model
- validation: validate_provider_config, validate_capabilities_consistency
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.inference.llm.definitions import (
    ProviderConfig,
    get_provider_config,
    is_provider_supported,
    list_providers,
    register_provider,
)
from src.inference.llm.definitions.model_registry import (
    clear_model_cache,
    get_model,
    get_models_by_provider,
    list_models,
    register_custom_model,
)
from src.inference.llm.definitions.model_types import (
    ModelCapability,
    ModelType,
)
from src.inference.llm.definitions.validation import (
    validate_capabilities_consistency,
    validate_provider_config,
)


class TestProviderRegistry:
    """Provider注册表测试."""

    def test_get_known_provider(self) -> None:
        config = get_provider_config("openai")
        assert config.name == "openai"
        assert config.requires_auth is True

    def test_get_local_provider(self) -> None:
        config = get_provider_config("local")
        assert config.name == "local"
        assert config.requires_auth is False

    def test_get_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="不支持的Provider"):
            get_provider_config("unknown_provider")

    def test_case_insensitive(self) -> None:
        config = get_provider_config("OpenAI")
        assert config.name == "openai"

    def test_is_provider_supported(self) -> None:
        assert is_provider_supported("openai") is True
        assert is_provider_supported("unknown") is False

    def test_list_providers(self) -> None:
        providers = list_providers()
        assert "openai" in providers
        assert "deepseek" in providers
        assert "local" in providers

    def test_register_custom_provider(self) -> None:
        config = ProviderConfig(
            name="custom",
            base_url="http://custom:8080/v1",
            requires_auth=False,
        )
        register_provider("custom", config)
        assert is_provider_supported("custom")
        fetched = get_provider_config("custom")
        assert fetched.name == "custom"

    def test_get_effective_base_url_env(self) -> None:
        config = ProviderConfig(
            name="test",
            base_url="http://default",
            base_url_env="TEST_BASE_URL",
        )
        with patch.dict("os.environ", {"TEST_BASE_URL": "http://override"}):
            assert config.get_effective_base_url() == "http://override"

    def test_get_effective_base_url_default(self) -> None:
        config = ProviderConfig(
            name="test",
            base_url="http://default",
        )
        assert config.get_effective_base_url() == "http://default"


class TestModelRegistry:
    """模型注册表测试."""

    def teardown_method(self) -> None:
        clear_model_cache()

    def test_list_models_not_empty(self) -> None:
        models = list_models()
        assert len(models) > 0

    def test_get_known_model(self) -> None:
        models = list_models()
        if models:
            model = get_model(models[0])
            assert model is not None

    def test_get_unknown_model(self) -> None:
        assert get_model("nonexistent:model") is None

    def test_get_models_by_provider(self) -> None:
        models = get_models_by_provider("openai")
        assert isinstance(models, list)

    def test_register_custom_model(self) -> None:
        from src.inference.llm.definitions.metadata import ModelMetadata

        model = ModelMetadata(
            id="test:test-model",
            name="Test Model",
            provider="test",
            model_type=ModelType.CHAT,
            description="Test",
            model_params={
                "temperature": {"default": 0.7, "min": 0.0, "max": 2.0},
                "top_p": {"default": 0.9, "min": 0.0, "max": 1.0},
                "num_predict": {"default": 1024, "min": 1, "max": 2048},
            },
            capabilities=[ModelCapability.TEXT_INPUT, ModelCapability.REASONING],
        )
        register_custom_model(model)
        assert get_model("test:test-model") is not None

    def test_register_replaces_existing(self) -> None:
        from src.inference.llm.definitions.metadata import ModelMetadata

        model1 = ModelMetadata(
            id="test:replace-test",
            name="V1",
            provider="test",
            model_type=ModelType.CHAT,
            description="V1",
            model_params={
                "temperature": {"default": 0.7, "min": 0.0, "max": 2.0},
                "top_p": {"default": 0.9, "min": 0.0, "max": 1.0},
                "num_predict": {"default": 1024, "min": 1, "max": 2048},
            },
            capabilities=[ModelCapability.TEXT_INPUT, ModelCapability.REASONING],
        )
        model2 = ModelMetadata(
            id="test:replace-test",
            name="V2",
            provider="test",
            model_type=ModelType.CHAT,
            description="V2",
            model_params={
                "temperature": {"default": 0.7, "min": 0.0, "max": 2.0},
                "top_p": {"default": 0.9, "min": 0.0, "max": 1.0},
                "num_predict": {"default": 2048, "min": 1, "max": 4096},
            },
            capabilities=[ModelCapability.TEXT_INPUT, ModelCapability.REASONING],
        )
        register_custom_model(model1)
        register_custom_model(model2)
        assert get_model("test:replace-test").name == "V2"

    def test_clear_cache(self) -> None:
        clear_model_cache()
        models_after = list_models()
        assert isinstance(models_after, list)


class TestValidation:
    """验证函数测试."""

    def test_validate_known_provider(self) -> None:
        validate_provider_config("openai")

    def test_validate_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="不支持的供应商"):
            validate_provider_config("nonexistent_provider_xyz")

    def test_validate_capabilities_consistency_valid(self) -> None:
        validate_capabilities_consistency(
            [ModelCapability.TEXT_INPUT, ModelCapability.REASONING],
        )

    def test_validate_image_gen_without_reasoning(self) -> None:
        validate_capabilities_consistency([ModelCapability.IMAGE_GENERATION])

    def test_validate_tool_calling_without_reasoning(self) -> None:
        with pytest.raises(ValueError, match="工具调用能力需要推理能力"):
            validate_capabilities_consistency(
                [ModelCapability.TOOL_CALLING],
            )

    def test_validate_tool_calling_with_reasoning(self) -> None:
        validate_capabilities_consistency(
            [ModelCapability.TOOL_CALLING, ModelCapability.REASONING],
        )
