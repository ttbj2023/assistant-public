"""EmbeddingsFactory 单元测试.

测试职责: 验证 Embeddings 实例工厂的核心功能逻辑 (从 LlmFactory 迁移)
测试范围: 实例创建/缓存复用/provider 路由/错误处理
Mock 策略: Mock format 实现类 / 缓存系统 / 元数据查询, 保留工厂业务逻辑
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from src.inference.embeddings.factory import EmbeddingsFactory


def _make_emb_metadata(provider: str = "local", model_id: str = "local:bge-m3"):
    """构造嵌入模型 metadata mock."""
    m = Mock()
    m.provider = provider
    m.id = model_id
    m.is_chat_model.return_value = False
    m.is_embedding_model.return_value = True
    return m


class TestEmbeddingsFactoryGet:
    """测试 EmbeddingsFactory.get_embeddings."""

    @pytest.fixture
    def mock_cache(self):
        cache = Mock()
        cache.get_embedding_client.return_value = None
        cache.cache_embedding_client.return_value = None
        return cache

    def test_cache_hit_should_return_cached_client(self, mock_cache):
        """缓存命中应直接返回."""
        mock_cached = Mock()
        mock_cache.get_embedding_client.return_value = mock_cached

        with patch(
            "src.inference.embeddings.factory.get_client_cache",
            return_value=mock_cache,
        ):
            factory = EmbeddingsFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            result = factory.get_embeddings("local:bge-m3")

            assert result is mock_cached
            mock_cache.cache_embedding_client.assert_not_called()

    def test_cache_miss_should_create_and_cache(self, mock_cache):
        """缓存未命中应创建新实例并写入缓存."""
        mock_emb = Mock()
        metadata = _make_emb_metadata()

        with (
            patch(
                "src.inference.embeddings.factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch(
                "src.inference.embeddings.factory.get_model",
                return_value=metadata,
            ),
            patch(
                "src.inference.embeddings.factory.OpenAIFormatEmbeddings",
                return_value=mock_emb,
            ),
        ):
            factory = EmbeddingsFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            result = factory.get_embeddings("local:bge-m3")

            assert result is mock_emb
            mock_cache.cache_embedding_client.assert_called_once()

    def test_unknown_model_should_raise_value_error(self, mock_cache):
        """未知模型应抛 ValueError."""
        with (
            patch(
                "src.inference.embeddings.factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch("src.inference.embeddings.factory.get_model", return_value=None),
        ):
            factory = EmbeddingsFactory()
            factory._cache = mock_cache  # type: ignore[assignment]

            with pytest.raises(ValueError, match="模型不存在"):
                factory.get_embeddings("unknown:model")

    def test_non_embedding_model_should_raise_value_error(self, mock_cache):
        """非嵌入模型应抛 ValueError."""
        metadata = Mock()
        metadata.is_embedding_model.return_value = False

        with (
            patch(
                "src.inference.embeddings.factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch(
                "src.inference.embeddings.factory.get_model",
                return_value=metadata,
            ),
        ):
            factory = EmbeddingsFactory()
            factory._cache = mock_cache  # type: ignore[assignment]

            with pytest.raises(ValueError, match="不是嵌入模型"):
                factory.get_embeddings("openai:gpt-4")


class TestEmbeddingsFactoryProviderRouting:
    """测试 _build_embeddings 的 provider 路由."""

    @pytest.fixture
    def mock_cache(self):
        cache = Mock()
        cache.get_embedding_client.return_value = None
        cache.cache_embedding_client.return_value = None
        return cache

    def test_local_provider_should_use_openai_format(self, mock_cache):
        """local provider 应使用 OpenAIFormatEmbeddings."""
        metadata = _make_emb_metadata("local", "local:bge-m3")

        with (
            patch(
                "src.inference.embeddings.factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch(
                "src.inference.embeddings.factory.get_model",
                return_value=metadata,
            ),
            patch(
                "src.inference.embeddings.factory.OpenAIFormatEmbeddings",
            ) as mock_cls,
            patch(
                "src.inference.embeddings.factory.get_provider_config",
            ),
            patch(
                "src.inference.embeddings.factory.get_http_pool",
            ) as mock_pool,
        ):
            factory = EmbeddingsFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            factory._http_pool = mock_pool  # type: ignore[assignment]
            factory.get_embeddings("local:bge-m3")

            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "bge-m3"
            assert call_kwargs["api_key"] is None

    def test_gemini_provider_should_use_gemini_format(self, mock_cache):
        """gemini provider 应使用 GeminiFormatEmbeddings."""
        metadata = _make_emb_metadata("gemini", "gemini:embedding-001")

        with (
            patch.dict(
                "os.environ",
                {"GEMINI_API_KEY": "test-key", "GEMINI_BASE_URL": "https://gemini"},
            ),
            patch(
                "src.inference.embeddings.factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch(
                "src.inference.embeddings.factory.get_model",
                return_value=metadata,
            ),
            patch(
                "src.inference.embeddings.factory.GeminiFormatEmbeddings",
            ) as mock_cls,
            patch(
                "src.inference.embeddings.factory.get_http_pool",
            ) as mock_pool,
        ):
            factory = EmbeddingsFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            factory._http_pool = mock_pool  # type: ignore[assignment]
            factory.get_embeddings("gemini:embedding-001")

            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "embedding-001"
            assert call_kwargs["api_key"] == "test-key"
