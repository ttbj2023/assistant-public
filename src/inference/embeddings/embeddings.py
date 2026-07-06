"""统一嵌入模型接口 - 委托 EmbeddingsFactory 实现实例复用."""

from __future__ import annotations

import logging

from langchain_core.embeddings import Embeddings

from src.config.inference_config import get_config
from src.inference.embeddings.factory import get_embeddings_factory

logger = logging.getLogger(__name__)


def create_embeddings(provider: str = "local", model: str | None = None) -> Embeddings:
    """创建嵌入模型实例 - 解析 model_id 并委托 EmbeddingsFactory.

    Args:
        provider: provider 名称 (local / openai), 仅在 model 未指定时用于解析默认 model_id
        model: 完整模型 ID, 若提供则直接使用

    Returns:
        Embeddings 实例

    """
    if model is None:
        inference_config = get_config()
        if provider == "local":
            model_id = inference_config.embeddings.model
        elif provider == "openai":
            model_id = "openai:text-embedding-3-small"
        else:
            raise ValueError(
                f"不支持的 provider: {provider}, 支持的供应商: local, openai",
            )
    else:
        model_id = model

    logger.info("📋 获取嵌入模型客户端: %s", model_id)

    return get_embeddings_factory().get_embeddings(model_id)


def get_embedding_info(embeddings: Embeddings) -> str:
    """获取嵌入模型信息."""
    if hasattr(embeddings, "model"):
        return f"Embeddings: {embeddings.model}"
    return "Embeddings: unknown"
