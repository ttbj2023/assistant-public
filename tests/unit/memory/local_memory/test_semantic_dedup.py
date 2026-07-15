"""cosine_similarity 单元测试.

覆盖: 数值正确性 (同向/正交/反向/零向量).
"""

from __future__ import annotations

import pytest

from src.agent.memory.local_memory.semantic_dedup import cosine_similarity


def test_cosine_similarity_identical_vectors_returns_one() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_returns_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite_returns_minus_one() -> None:
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_returns_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
