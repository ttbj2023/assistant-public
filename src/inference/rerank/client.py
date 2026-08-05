"""Rerank 客户端 - Jina/Cohere 风格 /rerank 端点适配.

对接独立部署的 reranker-service (Qwen3-Reranker-0.6B),
失败时静默降级返回 None, 不中断调用方检索流程.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from src.core.http_pool import get_http_pool

logger = logging.getLogger(__name__)

_PROVIDER_NAME = "local-reranker"


@dataclass(frozen=True, slots=True)
class RerankResult:
    """单条重排序结果.

    Attributes:
        index: 指向原始 documents 数组的位置
        relevance_score: [0.0, 1.0] sigmoid 概率, 越大越相关
    """

    index: int
    relevance_score: float


class RerankClient:
    """异步 Rerank 客户端.

    Args:
        base_url: reranker 服务地址 (如 http://localhost:8768)
        timeout: 请求超时秒数
        http_client: 可选, 注入自定义 httpx.AsyncClient (测试用)
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = http_client or get_http_pool().get(_PROVIDER_NAME)

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int | None = None,
        instruction: str | None = None,
    ) -> list[RerankResult] | None:
        """对候选文档重排序.

        Args:
            query: 用户查询
            documents: 候选文档文本列表
            top_n: 返回前 N 条 (None 表示全部)
            instruction: 任务指令, 影响打分精度

        Returns:
            按 relevance_score 降序的结果列表; 任何异常时返回 None (静默降级)
        """
        body: dict = {"query": query, "documents": documents}
        if top_n is not None:
            body["top_n"] = top_n
        if instruction is not None:
            body["instruction"] = instruction

        try:
            resp = await self._client.post(
                f"{self._base_url}/rerank",
                json=body,
                timeout=self._timeout,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Rerank 服务返回非 200: %d, 降级为原始排序",
                    resp.status_code,
                )
                return None

            data = resp.json()
            raw_results = data.get("results")
            if not isinstance(raw_results, list):
                logger.warning("Rerank 响应缺少 results 字段, 降级为原始排序")
                return None

            return [
                RerankResult(
                    index=item["index"],
                    relevance_score=item["relevance_score"],
                )
                for item in raw_results
            ]
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            logger.warning("Rerank 调用异常, 降级为原始排序: %s", exc)
            return None

    async def health_check(self) -> bool:
        """检查 reranker 服务是否可用."""
        try:
            resp = await self._client.get(
                f"{self._base_url}/health",
                timeout=5.0,
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
