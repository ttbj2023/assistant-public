"""业务 Embeddings 实现 - OpenAI/Gemini 格式适配.

Layer 3 业务层, 依赖 core.http_pool 提供 httpx 连接复用.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, override

import httpx
from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)


class OpenAIFormatEmbeddings(Embeddings):
    """统一的 OpenAI 格式嵌入客户端 - 纯异步实现.

    支持所有兼容 OpenAI 格式的嵌入端点 (本地和云端), 使用复用的 HTTPX 异步客户端.
    统一 API 格式, 支持各种遵循 OpenAI 标准的嵌入服务.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: int = 60,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url.endswith("/v1"):
            self.base_url = base_url.rstrip("/") + "/v1"
        else:
            self.base_url = base_url.rstrip("/")

        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._owns_client = http_client is None

        if http_client:
            self._client = http_client
        else:
            limits = httpx.Limits(
                max_keepalive_connections=10,
                max_connections=50,
                keepalive_expiry=30.0,
            )
            self._client = httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
            )

        logger.info(
            "🔧 初始化 OpenAI 格式嵌入客户端: model=%s, base_url=%s",
            model,
            self.base_url,
        )

    def _get_headers(self) -> dict[str, str]:
        """构建请求头."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _request_embeddings(self, texts: list[str]) -> list[list[float]]:
        """异步请求嵌入 - 使用复用的 HTTP 客户端.

        对 503/5xx/429/连接错误/超时做指数退避重试(复用 retry_config.http_api),
        兜住 Ollama 等本地服务的瞬时抖动.
        """
        from src.config.retry_config import get_http_retry_params
        from src.inference.usage import arecord_embedding_usage

        retry = get_http_retry_params()
        max_retries = retry["max_retries"]
        retryable_status = retry["retryable_status"]
        base_delay = retry["base_delay"]
        rate_limit_delay = retry["rate_limit_delay"]

        started_at = time.time()
        usage: dict | None = None
        success = False
        try:
            for attempt in range(1, max_retries + 1):
                try:
                    response = await self._client.post(
                        f"{self.base_url}/embeddings",
                        json={
                            "model": self.model,
                            "input": texts,
                        },
                        headers=self._get_headers(),
                    )
                    response.raise_for_status()
                    data = response.json()
                    usage = (
                        data.get("usage")
                        if isinstance(data.get("usage"), dict)
                        else None
                    )
                    success = True
                    return [item["embedding"] for item in data["data"]]
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    if status not in retryable_status or attempt == max_retries:
                        raise
                    delay = rate_limit_delay if status == 429 else base_delay * attempt
                    logger.warning(
                        "嵌入请求失败(status=%s), %s/%s 重试, 等待 %.1fs",
                        status,
                        attempt,
                        max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                except (httpx.ConnectError, httpx.TimeoutException) as e:
                    if attempt == max_retries:
                        raise
                    delay = base_delay * attempt
                    logger.warning(
                        "嵌入请求连接异常(%s), %s/%s 重试, 等待 %.1fs",
                        type(e).__name__,
                        attempt,
                        max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
            # max_retries<1 时循环不执行, 需显式兜底
            raise RuntimeError("嵌入重试循环未产生结果")
        finally:
            await arecord_embedding_usage(
                provider="openai_compatible",
                model_id=self.model,
                texts=texts,
                raw_usage=usage,
                duration_ms=max(int((time.time() - started_at) * 1000), 0),
                success=success,
            )

    @override
    def embed_query(self, text: str) -> list[float]:
        """嵌入单个查询文本 - 同步接口通过异步实现."""
        return self.embed_documents([text])[0]

    @override
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """嵌入多个文档文本 - 同步接口通过异步实现.

        使用 run_async_in_sync_context 替代 asyncio.run,
        避免在嵌套事件循环环境中失败 (如线程池执行器内调用).
        """
        import functools

        from src.utils.async_utils import run_async_in_sync_context

        return run_async_in_sync_context(  # pyright: ignore[reportReturnType]
            functools.partial(self._request_embeddings, texts),
        )

    @override
    async def aembed_query(self, text: str) -> list[float]:
        """异步嵌入单个查询文本."""
        result = await self.aembed_documents([text])
        return result[0]

    @override
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """异步嵌入多个文档文本."""
        return await self._request_embeddings(texts)

    async def close(self) -> None:
        """关闭自建的 HTTP 客户端连接(共享客户端不关闭)."""
        if self._owns_client and hasattr(self, "_client"):
            await self._client.aclose()
            logger.debug(f"🔌 关闭 OpenAI 格式嵌入客户端: {self.model}")


class GeminiFormatEmbeddings(Embeddings):
    """Google Gemini 原生 API 嵌入客户端.

    使用 /v1beta/models/{model}:embedContent 端点,
    支持多模态嵌入, 复用共享 HTTPX 异步客户端.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout: int = 60,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._owns_client = http_client is None

        if http_client:
            self._client = http_client
        else:
            limits = httpx.Limits(
                max_keepalive_connections=10,
                max_connections=50,
                keepalive_expiry=30.0,
            )
            self._client = httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
            )

        logger.info(
            f"🔧 初始化 Gemini 原生嵌入客户端: model={model}, base_url={self.base_url}",
        )

    def _get_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    async def _embed_single(self, text: str) -> list[float]:
        url = f"{self.base_url}/v1beta/models/{self.model}:embedContent"
        payload: dict[str, Any] = {
            "model": f"models/{self.model}",
            "content": {"parts": [{"text": text}]},
        }
        response = await self._client.post(
            url,
            json=payload,
            headers=self._get_headers(),
        )
        response.raise_for_status()
        data = response.json()
        return data["embedding"]["values"]

    async def _request_embeddings(self, texts: list[str]) -> list[list[float]]:
        from src.inference.usage import arecord_embedding_usage

        started_at = time.time()
        success = False
        try:
            tasks = [self._embed_single(t) for t in texts]
            result = list(await asyncio.gather(*tasks))
            success = True
            return result
        finally:
            await arecord_embedding_usage(
                provider="gemini",
                model_id=self.model,
                texts=texts,
                raw_usage=None,
                duration_ms=max(int((time.time() - started_at) * 1000), 0),
                success=success,
            )

    @override
    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    @override
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        import functools

        from src.utils.async_utils import run_async_in_sync_context

        return run_async_in_sync_context(  # pyright: ignore[reportReturnType]
            functools.partial(self._request_embeddings, texts),
        )

    @override
    async def aembed_query(self, text: str) -> list[float]:
        result = await self.aembed_documents([text])
        return result[0]

    @override
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._request_embeddings(texts)

    async def close(self) -> None:
        """关闭自建的 HTTP 客户端连接(共享客户端不关闭)."""
        if self._owns_client and hasattr(self, "_client"):
            await self._client.aclose()
            logger.debug(f"🔌 关闭 Gemini 原生嵌入客户端: {self.model}")
