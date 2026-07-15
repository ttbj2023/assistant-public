"""语义相似度计算 helper.

提供余弦相似度计算, 服务于 index_run_service 的语义连续性判定.
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """计算两向量的余弦相似度, 任一为零向量返回 0.0."""
    dot = sum(a * b for a, b in zip(vec1, vec2, strict=False))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 < 1e-12 or norm2 < 1e-12:
        return 0.0
    return dot / (norm1 * norm2)


__all__ = ["cosine_similarity"]
