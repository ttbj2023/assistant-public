"""Embeddings 实例工厂 - 业务知识集中层.

从 LlmFactory 迁移而来, 让 embeddings 包自给自足 (入口 + 工厂 + 实现),
打破与 llm 包的循环依赖. 缓存与 http_pool 与 LlmFactory 共享同一进程级单例.

Layer 3 业务层, 依赖 core.cache / core.http_pool,
以及 llm.definitions (provider 配置 / 模型元数据) 提供模型元数据.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import cast

from langchain_core.embeddings import Embeddings

from src.core.cache import get_client_cache
from src.core.http_pool import get_http_pool
from src.inference.embeddings.formats import (
    GeminiFormatEmbeddings,
    OpenAIFormatEmbeddings,
)
from src.inference.llm.definitions import ModelMetadata, get_provider_config
from src.inference.llm.definitions.model_registry import get_model
from src.inference.shared.provider_validation import (
    format_error_message,
    validate_supported_provider,
)

logger = logging.getLogger(__name__)

SUPPORTED_EMBEDDINGS_PROVIDERS = ["local", "local-embedding", "openai", "gemini"]

DEFAULT_EMBEDDINGS_TIMEOUT = 10.0


class EmbeddingsFactory:
    """Embeddings 实例工厂 - 缓存优先 + provider 路由."""

    def __init__(self) -> None:
        self._http_pool = get_http_pool()
        self._cache = get_client_cache()

    def get_embeddings(self, model_id: str) -> Embeddings:
        """缓存优先的 Embeddings 实例获取.

        Args:
            model_id: 模型 ID, 格式为 "provider:model_name"

        Returns:
            Embeddings 实例

        """
        cached = self._cache.get_embedding_client(model_id)
        if cached:
            logger.debug("♻️  复用嵌入模型客户端: %s", model_id)
            return cast("Embeddings", cached)

        logger.info("🔧 创建新的嵌入模型客户端: %s", model_id)
        metadata = get_model(model_id)
        if metadata is None:
            raise ValueError(
                format_error_message("SYSTEM", "模型不存在", model_id),
            )
        if not metadata.is_embedding_model():
            raise ValueError(
                format_error_message(
                    "SYSTEM",
                    "模型类型错误",
                    f"{model_id} 不是嵌入模型",
                ),
            )

        client = self._build_embeddings(metadata)
        self._cache.cache_embedding_client(model_id, client)
        return client

    def _build_embeddings(self, metadata: ModelMetadata) -> Embeddings:
        """构造 Embeddings 客户端实例 - provider 路由.

        API Key 和 Base URL 统一从 src.inference.llm.definitions 获取.
        """
        provider = metadata.provider
        model_name = metadata.id.split(":", 1)[1]

        validate_supported_provider(provider, SUPPORTED_EMBEDDINGS_PROVIDERS)

        provider_cfg = get_provider_config(provider)

        if provider in {"local", "local-embedding"}:
            base_url = provider_cfg.get_effective_base_url()

            logger.info(
                "🔧 创建本地嵌入客户端 (OpenAI 格式): model_name=%s, base_url=%s",
                model_name,
                base_url,
            )

            shared_client = self._http_pool.get(provider)

            return OpenAIFormatEmbeddings(
                base_url=base_url,
                model=model_name,
                api_key=None,
                timeout=int(DEFAULT_EMBEDDINGS_TIMEOUT),
                http_client=shared_client,
            )

        if provider == "openai":
            base_url = provider_cfg.get_effective_base_url()
            try:
                api_key = provider_cfg.require_api_key()
            except RuntimeError as e:
                raise ValueError(
                    format_error_message(
                        "OPENAI",
                        "配置错误",
                        str(e),
                    ),
                ) from e

            logger.info(
                "🔧 创建 OpenAI 嵌入客户端 (OpenAI 格式): model_name=%s, base_url=%s",
                model_name,
                base_url,
            )

            shared_client = self._http_pool.get(provider)

            return OpenAIFormatEmbeddings(
                base_url=base_url,
                model=model_name,
                api_key=api_key,
                timeout=int(DEFAULT_EMBEDDINGS_TIMEOUT),
                http_client=shared_client,
            )

        if provider == "gemini":
            base_url = provider_cfg.get_effective_base_url()
            if not base_url:
                raise ValueError(
                    format_error_message(
                        "GEMINI",
                        "配置错误",
                        "需要设置 GEMINI_BASE_URL 环境变量",
                    ),
                )

            try:
                api_key = provider_cfg.require_api_key()
            except RuntimeError as e:
                raise ValueError(
                    format_error_message(
                        "GEMINI",
                        "配置错误",
                        str(e),
                    ),
                ) from e

            logger.info(
                "🔧 创建 Gemini 嵌入客户端 (原生 API): model_name=%s, base_url=%s",
                model_name,
                base_url,
            )

            shared_client = self._http_pool.get(provider)

            return GeminiFormatEmbeddings(
                base_url=base_url,
                model=model_name,
                api_key=api_key,
                timeout=int(DEFAULT_EMBEDDINGS_TIMEOUT),
                http_client=shared_client,
            )

        raise ValueError(
            format_error_message(
                provider.upper(),
                "不支持",
                f"不支持的嵌入模型 provider: {provider}",
            ),
        )


@lru_cache(maxsize=1)
def get_embeddings_factory() -> EmbeddingsFactory:
    """返回进程级 EmbeddingsFactory 单例 (与 LlmFactory 共享 cache/http_pool)."""
    return EmbeddingsFactory()
