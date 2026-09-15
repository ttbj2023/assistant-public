#!/usr/bin/env python3
"""Builtin Models单元测试.

测试create_builtin_models()的输出质量和模型分组配置.
模型注册表查询(get_model/list_models等)在test_llm_definitions.py中测试.
"""

from __future__ import annotations

import pytest

from src.inference.llm.definitions import (
    ModelCapability,
    ModelType,
    create_builtin_models,
)


class TestCreateBuiltinModels:
    """验证create_builtin_models()的输出质量"""

    def test_should_configure_embedding_models_correctly(self) -> None:
        """嵌入模型应为确定性模型, 不需要采样参数"""
        models = create_builtin_models()
        embedding_models = [m for m in models if m.model_type == ModelType.EMBEDDING]

        assert len(embedding_models) >= 2

        for model in embedding_models:
            assert "temperature" not in model.model_params
            assert ModelCapability.TEXT_INPUT in model.capabilities

    def test_should_include_deepseek_flash_with_image_input(self) -> None:
        """DeepSeek V4.1 Flash 应内置且声明图片输入能力(原生多模态)"""
        models = create_builtin_models()
        by_id = {m.id: m for m in models}

        flash = by_id.get("deepseek:deepseek-flash")

        assert flash is not None
        assert flash.provider == "deepseek"
        assert flash.model_type == ModelType.CHAT
        assert ModelCapability.IMAGE_INPUT in flash.capabilities
        assert ModelCapability.TOOL_CALLING in flash.capabilities

    def test_should_retire_replaced_deepseek_v4_flash_models(self) -> None:
        """已退役的 V4-Flash/V4-Flash-Vision-Exp 不应再内置(V4.1-Flash 接管)"""
        ids = {m.id for m in create_builtin_models()}

        assert "deepseek:deepseek-v4-flash" not in ids
        assert "deepseek:deepseek-v4-flash-vision-exp" not in ids

    def test_should_price_deepseek_models_at_peak_tier(self) -> None:
        """DeepSeek 系列定价应记录高峰档, 与官方峰谷分时定价对齐"""
        models = create_builtin_models()
        by_id = {m.id: m for m in models}

        expected = {
            "deepseek:deepseek-flash": (0.3, 1.2, 0.006),
            "deepseek:deepseek-v4-pro": (1.32, 3.96, 0.044),
        }

        for model_id, (price_in, price_out, price_cached) in expected.items():
            pricing = by_id[model_id].pricing
            assert pricing is not None, model_id
            assert pricing.currency == "USD", model_id
            assert (
                pricing.input,
                pricing.output,
                pricing.cached_input,
            ) == (price_in, price_out, price_cached), model_id


pytestmark_unit = pytest.mark.unit
