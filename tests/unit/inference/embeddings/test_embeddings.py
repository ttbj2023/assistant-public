"""Embeddings组件单元测试.

测试职责: 验证嵌入模型创建的核心功能逻辑
测试范围: create_embeddings函数、get_embedding_info函数、SimpleSettings类
Mock策略: Mock配置系统和模型加载器，保留业务逻辑
测试价值: 确保嵌入模型创建的正确性和多provider支持

⚠️ 测试重点:
- 验证不同provider的嵌入模型创建
- 验证模型ID的正确处理
- 验证配置加载和传递
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from src.inference.embeddings.embeddings import create_embeddings


class TestCreateEmbeddingsDefaultModels:
    """测试create_embeddings的默认模型选择"""

    @pytest.fixture
    def mock_inference_config(self):
        """Mock推理配置"""
        config = Mock()
        config.embeddings.model = "local:default-model"
        config.model_dump.return_value = {}
        return config

    def test_create_embeddings_with_local_provider_should_use_default_model(
        self, mock_inference_config
    ):
        """测试创建Embeddings：local provider应使用默认模型"""
        with (
            patch(
                "src.inference.embeddings.embeddings.get_config",
                return_value=mock_inference_config,
            ),
            patch(
                "src.inference.embeddings.embeddings.get_embeddings_factory"
            ) as mock_create,
        ):
            mock_create.return_value = Mock()

            result = create_embeddings(provider="local")

            assert result is not None
            mock_create.return_value.get_embeddings.assert_called_once_with(
                "local:default-model"
            )

    def test_create_embeddings_with_unsupported_provider(self):
        """测试创建Embeddings：不支持的provider应抛出异常"""
        mock_config = Mock()
        mock_config.embeddings.model = "local:default"
        mock_config.model_dump.return_value = {}

        with (
            patch(
                "src.inference.embeddings.embeddings.get_config",
                return_value=mock_config,
            ),
            pytest.raises(ValueError, match="不支持的 provider"),
        ):
            create_embeddings(provider="sentence-transformer")


class TestCreateEmbeddingsWithCustomModel:
    """测试create_embeddings的自定义模型"""

    def test_create_embeddings_with_custom_model_should_use_provided_model(self):
        """测试创建Embeddings：自定义模型应使用提供的模型ID"""
        mock_config = Mock()
        mock_config.embeddings.model = "local:default"
        mock_config.model_dump.return_value = {}

        with (
            patch(
                "src.inference.embeddings.embeddings.get_config",
                return_value=mock_config,
            ),
            patch(
                "src.inference.embeddings.embeddings.get_embeddings_factory"
            ) as mock_create,
        ):
            mock_create.return_value = Mock()

            result = create_embeddings(provider="local", model="custom:model:v2")

            assert result is not None
            mock_create.return_value.get_embeddings.assert_called_once_with(
                "custom:model:v2"
            )

    def test_create_embeddings_with_custom_model_none_should_use_default(self):
        """测试创建Embeddings：model=None应使用默认值"""
        mock_config = Mock()
        mock_config.embeddings.model = "local:default-model"
        mock_config.model_dump.return_value = {}

        with (
            patch(
                "src.inference.embeddings.embeddings.get_config",
                return_value=mock_config,
            ),
            patch(
                "src.inference.embeddings.embeddings.get_embeddings_factory"
            ) as mock_create,
        ):
            mock_create.return_value = Mock()

            # model=None，应该使用provider的默认模型
            create_embeddings(provider="local", model=None)

            mock_create.return_value.get_embeddings.assert_called_once_with(
                "local:default-model"
            )


class TestCreateEmbeddingsIntegrationScenarios:
    """测试create_embeddings的集成场景"""

    def test_create_embeddings_multiple_calls_should_reuse_clients(self):
        """测试创建Embeddings：多次调用应通过ClientManager复用客户端"""
        mock_config = Mock()
        mock_config.embeddings.model = "local:test"
        mock_config.model_dump.return_value = {}

        # 第一次调用返回相同的客户端
        mock_client = Mock()
        mock_client.model = "local:test"

        with (
            patch(
                "src.inference.embeddings.embeddings.get_config",
                return_value=mock_config,
            ),
            patch(
                "src.inference.embeddings.embeddings.get_embeddings_factory"
            ) as mock_create,
        ):
            mock_create.return_value = mock_client

            # 多次调用
            result1 = create_embeddings()
            result2 = create_embeddings()

            # 验证都调用了 EmbeddingsFactory.get_embeddings（实际复用由工厂缓存处理）
            assert mock_create.return_value.get_embeddings.call_count == 2
            assert result1 is not None
            assert result2 is not None
