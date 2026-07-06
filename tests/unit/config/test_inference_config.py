#!/usr/bin/env python3
"""推理配置模块单元测试.

只保留验证真实配置行为的测试: YAML 合并优先级、模型回退逻辑、配置来源.
纯 Pydantic 字段赋值/默认值回读的测试已删除(无业务逻辑).
"""

from __future__ import annotations

from unittest.mock import patch

from src.config.inference_config import (
    ExpertsConfig,
    FallbackModelConfig,
    InferenceConfig,
)


class TestExpertsConfig:
    """专家工具配置测试类."""

    def test_url_context_defaults_should_be_valid(self) -> None:
        """URL Context 默认配置应可直接使用."""
        config = ExpertsConfig()

        assert config.url_context_enabled is True
        url_context_model = config.get_model_id("url_context")
        assert url_context_model is not None and ":" in url_context_model
        assert isinstance(config.url_context_quick_timeout, (int, float))
        assert config.url_context_quick_timeout > 0
        assert isinstance(config.url_context_deep_timeout, (int, float))
        assert config.url_context_deep_timeout > 0
        assert isinstance(config.url_context_max_urls, int)
        assert config.url_context_max_urls > 0

    def test_web_research_synthesis_should_fallback_to_default_model(self) -> None:
        """综合模型为空时应回退专家默认模型."""
        config = ExpertsConfig(default_model="deepseek:test")

        assert config.get_model_id("web_research_synthesis") == "deepseek:test"

    def test_url_context_yaml_config_should_apply(self) -> None:
        """YAML 字典应能覆盖 URL Context 配置."""
        config = InferenceConfig.from_dict({
            "experts": {
                "url_context_enabled": False,
                "url_context_model": "gemini:test-url-context",
                "url_context_max_urls": 8,
            }
        })

        assert config.experts.url_context_enabled is False
        assert config.experts.get_model_id("url_context") == "gemini:test-url-context"
        assert config.experts.url_context_max_urls == 8


class TestFallbackModelConfig:
    """fallback 模型配置测试类."""

    @patch("src.config.inference_config.get_module_config_sync")
    def test_fallback_config_yaml_override_should_work(
        self,
        mock_get_yaml_config,
    ) -> None:
        """YAML 覆盖 fallback 配置时, 未指定字段保留默认值."""
        mock_get_yaml_config.return_value = {
            "fallback": {
                "text_model": "yaml:text",
            },
        }

        config = InferenceConfig.from_module_config()

        assert config.fallback.text_model == "yaml:text"
        assert config.fallback.vision_model == FallbackModelConfig().vision_model


class TestConfigurationIntegration:
    """配置集成测试类."""

    @patch("src.config.inference_config.get_module_config_sync")
    def test_yaml_config_is_inference_source(self, mock_get_yaml_config) -> None:
        """推理配置来源是 YAML/defaults, 不是通用环境变量 overlay."""
        mock_get_yaml_config.return_value = {
            "embeddings": {"model": "local-embedding:bge-m3"},
        }

        config = InferenceConfig.from_module_config()

        assert config.embeddings.model == "local-embedding:bge-m3"
