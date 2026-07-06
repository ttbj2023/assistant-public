"""Embeddings格式适配器单元测试.

测试OpenAI/Gemini格式嵌入客户端的核心逻辑, Mock HTTP请求.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.inference.embeddings.formats import (
    GeminiFormatEmbeddings,
    OpenAIFormatEmbeddings,
)


class TestOpenAIFormatEmbeddingsInit:
    def test_should_append_v1_when_missing(self):
        """URL不含/v1时应自动追加."""
        emb = OpenAIFormatEmbeddings(
            base_url="https://api.example.com",
            model="text-embedding-3-small",
        )
        assert emb.base_url == "https://api.example.com/v1"

    def test_should_not_duplicate_v1(self):
        """URL已含/v1时不应重复追加."""
        emb = OpenAIFormatEmbeddings(
            base_url="https://api.example.com/v1",
            model="text-embedding-3-small",
        )
        assert emb.base_url == "https://api.example.com/v1"

    def test_should_strip_trailing_slash(self):
        """应去除末尾斜杠."""
        emb = OpenAIFormatEmbeddings(
            base_url="https://api.example.com/",
            model="test-model",
        )
        assert not emb.base_url.endswith("//")

    def test_should_use_custom_http_client(self):
        """传入http_client时应使用该客户端."""
        mock_client = MagicMock()
        emb = OpenAIFormatEmbeddings(
            base_url="https://api.example.com",
            model="test-model",
            http_client=mock_client,
        )
        assert emb._client is mock_client

    def test_should_create_default_http_client_when_none(self):
        """未传入http_client时应创建默认客户端."""
        emb = OpenAIFormatEmbeddings(
            base_url="https://api.example.com",
            model="test-model",
        )
        assert emb._client is not None


class TestOpenAIFormatEmbeddingsHeaders:
    def test_should_include_auth_header_when_key_set(self):
        """设置API Key时应包含Authorization头."""
        emb = OpenAIFormatEmbeddings(
            base_url="https://api.example.com/v1",
            model="test-model",
            api_key="sk-test123",
        )
        headers = emb._get_headers()
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer sk-test123"

    def test_should_skip_auth_header_when_no_key(self):
        """无API Key时不应包含Authorization头."""
        emb = OpenAIFormatEmbeddings(
            base_url="https://api.example.com/v1",
            model="test-model",
            api_key=None,
        )
        headers = emb._get_headers()
        assert "Authorization" not in headers

    def test_should_always_include_content_type(self):
        """应始终包含Content-Type头."""
        emb = OpenAIFormatEmbeddings(
            base_url="https://api.example.com/v1",
            model="test-model",
        )
        headers = emb._get_headers()
        assert headers["Content-Type"] == "application/json"


class TestOpenAIFormatEmbeddingsRequest:
    @pytest.mark.asyncio
    async def test_request_embeddings_should_post_to_endpoint(self):
        """应向/embeddings端点发送POST请求."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1, 0.2, 0.3]},
                {"embedding": [0.4, 0.5, 0.6]},
            ],
            "usage": {"prompt_tokens": 9, "total_tokens": 9},
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        emb = OpenAIFormatEmbeddings(
            base_url="https://api.example.com/v1",
            model="text-embedding-3-small",
            http_client=mock_client,
        )

        with patch(
            "src.inference.usage.arecord_embedding_usage",
        ) as record_usage:
            result = await emb._request_embeddings(["hello", "world"])

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "/embeddings" in call_args[0][0]
        assert len(result) == 2
        assert result[0] == [0.1, 0.2, 0.3]
        record_usage.assert_awaited_once()
        assert record_usage.call_args.kwargs["raw_usage"] == {
            "prompt_tokens": 9,
            "total_tokens": 9,
        }
        assert record_usage.call_args.kwargs["success"] is True

    @pytest.mark.asyncio
    async def test_aembed_query_should_return_single_vector(self):
        """aembed_query应返回单个向量."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1, 0.2, 0.3]}],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        emb = OpenAIFormatEmbeddings(
            base_url="https://api.example.com/v1",
            model="test-model",
            http_client=mock_client,
        )

        with patch("src.inference.usage.arecord_embedding_usage"):
            result = await emb.aembed_query("test query")

        assert result == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_aembed_documents_should_return_batch_vectors(self):
        """aembed_documents应返回批量向量."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1, 0.2]},
                {"embedding": [0.3, 0.4]},
                {"embedding": [0.5, 0.6]},
            ],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        emb = OpenAIFormatEmbeddings(
            base_url="https://api.example.com/v1",
            model="test-model",
            http_client=mock_client,
        )

        with patch("src.inference.usage.arecord_embedding_usage"):
            result = await emb.aembed_documents(["a", "b", "c"])

        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_close_should_aclose_client(self):
        """close应关闭HTTP客户端."""
        mock_client = AsyncMock()
        emb = OpenAIFormatEmbeddings(
            base_url="https://api.example.com/v1",
            model="test-model",
            http_client=mock_client,
        )

        await emb.close()

        mock_client.aclose.assert_called_once()


class TestGeminiFormatEmbeddingsInit:
    def test_should_store_config(self):
        """应正确存储配置参数."""
        emb = GeminiFormatEmbeddings(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-embedding-exp-03-07",
            api_key="test-key",
        )
        assert emb.model == "gemini-embedding-exp-03-07"
        assert emb.api_key == "test-key"
        assert not emb.base_url.endswith("/")

    def test_should_use_custom_http_client(self):
        """传入http_client时应使用该客户端."""
        mock_client = MagicMock()
        emb = GeminiFormatEmbeddings(
            base_url="https://example.com",
            model="test-model",
            api_key="key",
            http_client=mock_client,
        )
        assert emb._client is mock_client


class TestGeminiFormatEmbeddingsHeaders:
    def test_should_include_auth_header(self):
        """应始终包含Authorization头."""
        emb = GeminiFormatEmbeddings(
            base_url="https://example.com",
            model="test-model",
            api_key="test-key",
        )
        headers = emb._get_headers()
        assert headers["Authorization"] == "Bearer test-key"
        assert headers["Content-Type"] == "application/json"


class TestGeminiFormatEmbeddingsRequest:
    @pytest.mark.asyncio
    async def test_request_embeddings_should_call_embed_content(self):
        """应调用embedContent端点."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "embedding": {"values": [0.1, 0.2, 0.3]},
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        emb = GeminiFormatEmbeddings(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-embedding-exp-03-07",
            api_key="test-key",
            http_client=mock_client,
        )

        with patch(
            "src.inference.usage.arecord_embedding_usage",
        ) as record_usage:
            result = await emb._request_embeddings(["hello"])

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert ":embedContent" in call_args[0][0]
        assert result == [[0.1, 0.2, 0.3]]
        record_usage.assert_awaited_once()
        assert record_usage.call_args.kwargs["raw_usage"] is None
        assert record_usage.call_args.kwargs["success"] is True

    @pytest.mark.asyncio
    async def test_aembed_query_should_return_single_vector(self):
        """aembed_query应返回单个向量."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "embedding": {"values": [0.1, 0.2, 0.3]},
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        emb = GeminiFormatEmbeddings(
            base_url="https://example.com",
            model="test-model",
            api_key="key",
            http_client=mock_client,
        )

        with patch("src.inference.usage.arecord_embedding_usage"):
            result = await emb.aembed_query("test query")

        assert result == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_close_should_aclose_client(self):
        """close应关闭HTTP客户端."""
        mock_client = AsyncMock()
        emb = GeminiFormatEmbeddings(
            base_url="https://example.com",
            model="test-model",
            api_key="key",
            http_client=mock_client,
        )

        await emb.close()

        mock_client.aclose.assert_called_once()
