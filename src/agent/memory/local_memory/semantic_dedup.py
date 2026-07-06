"""置顶记忆语义去重 helper.

基于嵌入向量计算新条目与已有条目的余弦相似度, 判定是否语义重复.
仅服务于 add 去重(小模型换表述重复 add 的容错). 任何 embedding 调用失败
均降级为"不判重"(返回 False), 由调用方回退到精确字符串匹配, 不阻断主流程.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """计算两向量的余弦相似度, 任一为零向量返回 0.0."""
    dot = sum(a * b for a, b in zip(vec1, vec2, strict=False))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 < 1e-12 or norm2 < 1e-12:
        return 0.0
    return dot / (norm1 * norm2)


async def is_semantically_duplicate(
    new_content: str,
    existing_lines: list[str],
    embeddings: Embeddings,
    threshold: float,
) -> bool:
    """判断新条目是否与已有条目语义重复.

    取新条目与各已有条目余弦相似度的最大值, >= threshold 即判重.
    existing_lines 为空直接返回 False(无对比对象). embedding 调用异常时
    记 warning 并返回 False(不判重), 让调用方回退精确匹配.

    Args:
        new_content: 待新增的条目文本
        existing_lines: 同字段已有条目(逐行)
        embeddings: 嵌入模型实例(复用项目统一 create_embeddings)
        threshold: 余弦相似度阈值, 越高越严格

    Returns:
        是否语义重复

    """
    if not existing_lines:
        return False

    try:
        existing_vecs = await embeddings.aembed_documents(existing_lines)
        new_vec = await embeddings.aembed_query(new_content)
    except Exception as e:
        logger.warning("语义去重 embedding 调用失败, 回退精确匹配: %s", e)
        return False

    max_sim = max(
        (cosine_similarity(new_vec, v) for v in existing_vecs),
        default=0.0,
    )
    if max_sim >= threshold:
        logger.debug(
            "语义重复判定: similarity=%.4f >= threshold=%.2f, new=%s",
            max_sim,
            threshold,
            new_content[:50],
        )
        return True
    return False


__all__ = ["cosine_similarity", "is_semantically_duplicate"]
