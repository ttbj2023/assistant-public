"""RerankClient 单元测试.

Mock httpx 请求, 验证请求构造/响应解析/降级行为.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.inference.rerank.client import RerankClient, RerankResult


def _make_rerank_response(
    results: list[dict],
    model: str = "Qwen/Qwen3-Reranker-0.6B",
) -> httpx.Response:
    """构造 /rerank 端点的 httpx.Response."""
    request = httpx.Request("POST", "http://localhost:8768/rerank")
    return httpx.Response(
        200,
        json={"model": model, "results": results},
        request=request,
    )


@pytest.fixture
def mock_http_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def client(mock_http_client: AsyncMock) -> RerankClient:
    return RerankClient(
        base_url="http://localhost:8768",
        http_client=mock_http_client,
    )


class TestRerankBasicCall:
    """基本调用: 请求构造与响应解析."""

    @pytest.mark.asyncio
    async def test_rerank_success_returns_sorted_results(
        self,
        client: RerankClient,
        mock_http_client: AsyncMock,
    ):
        """成功调用应返回按 relevance_score 降序的 RerankResult 列表."""
        mock_http_client.post.return_value = _make_rerank_response(
            results=[
                {"index": 2, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.82},
                {"index": 1, "relevance_score": 0.10},
            ],
        )

        result = await client.rerank(
            query="什么是龙井茶",
            documents=["绿茶介绍", "物理力学", "龙井茶冲泡方法"],
        )

        assert result is not None
        assert len(result) == 3
        assert result[0] == RerankResult(index=2, relevance_score=0.95)
        assert result[1] == RerankResult(index=0, relevance_score=0.82)
        assert result[2] == RerankResult(index=1, relevance_score=0.10)

    @pytest.mark.asyncio
    async def test_rerank_sends_correct_request_body(
        self,
        client: RerankClient,
        mock_http_client: AsyncMock,
    ):
        """请求体应包含 query, documents, 以及可选的 top_n/instruction."""
        mock_http_client.post.return_value = _make_rerank_response(results=[])

        await client.rerank(
            query="测试查询",
            documents=["文档1"],
            top_n=5,
            instruction="Retrieve relevant conversations",
        )

        call_args = mock_http_client.post.call_args
        body = call_args.kwargs["json"]
        assert body["query"] == "测试查询"
        assert body["documents"] == ["文档1"]
        assert body["top_n"] == 5
        assert body["instruction"] == "Retrieve relevant conversations"

    @pytest.mark.asyncio
    async def test_rerank_omits_optional_fields_when_none(
        self,
        client: RerankClient,
        mock_http_client: AsyncMock,
    ):
        """未指定 top_n/instruction 时请求体不应包含这些字段."""
        mock_http_client.post.return_value = _make_rerank_response(results=[])

        await client.rerank(query="q", documents=["d"])

        body = mock_http_client.post.call_args.kwargs["json"]
        assert "top_n" not in body
        assert "instruction" not in body


class TestRerankDegradation:
    """降级行为: 异常时返回 None, 不中断调用方."""

    @pytest.mark.asyncio
    async def test_rerank_returns_none_on_http_error(
        self,
        client: RerankClient,
        mock_http_client: AsyncMock,
    ):
        """HTTP 错误 (如 500) 应返回 None."""
        mock_http_client.post.return_value = httpx.Response(
            500,
            json={"detail": {"error": "internal_error", "message": "boom"}},
            request=httpx.Request("POST", "http://localhost:8768/rerank"),
        )

        result = await client.rerank(query="q", documents=["d"])
        assert result is None

    @pytest.mark.asyncio
    async def test_rerank_returns_none_on_connection_error(
        self,
        client: RerankClient,
        mock_http_client: AsyncMock,
    ):
        """连接异常 (服务不可达) 应返回 None."""
        mock_http_client.post.side_effect = httpx.ConnectError("refused")

        result = await client.rerank(query="q", documents=["d"])
        assert result is None

    @pytest.mark.asyncio
    async def test_rerank_returns_none_on_timeout(
        self,
        client: RerankClient,
        mock_http_client: AsyncMock,
    ):
        """超时应返回 None."""
        mock_http_client.post.side_effect = httpx.ReadTimeout("timeout")

        result = await client.rerank(query="q", documents=["d"])
        assert result is None

    @pytest.mark.asyncio
    async def test_rerank_returns_none_on_invalid_json(
        self,
        client: RerankClient,
        mock_http_client: AsyncMock,
    ):
        """响应体格式异常应返回 None."""
        mock_http_client.post.return_value = httpx.Response(
            200,
            json={"unexpected": "format"},
            request=httpx.Request("POST", "http://localhost:8768/rerank"),
        )

        result = await client.rerank(query="q", documents=["d"])
        assert result is None


class TestRerankHealthCheck:
    """健康检查."""

    @pytest.mark.asyncio
    async def test_health_check_returns_true_when_ok(
        self,
        client: RerankClient,
        mock_http_client: AsyncMock,
    ):
        """服务正常时返回 True."""
        mock_http_client.get.return_value = httpx.Response(
            200,
            json={"status": "ok", "model": "Qwen/Qwen3-Reranker-0.6B", "loaded": True},
            request=httpx.Request("GET", "http://localhost:8768/health"),
        )

        assert await client.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_error(
        self,
        client: RerankClient,
        mock_http_client: AsyncMock,
    ):
        """服务不可达时返回 False."""
        mock_http_client.get.side_effect = httpx.ConnectError("refused")

        assert await client.health_check() is False
