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


pytestmark_unit = pytest.mark.unit
