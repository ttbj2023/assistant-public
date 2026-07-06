"""semantic_dedup helper 单元测试.

覆盖: cosine_similarity 数值正确性; is_semantically_duplicate 的空输入/
阈值边界/多候选取最大值/异常回退.
"""

from __future__ import annotations

import pytest

from src.agent.memory.local_memory.semantic_dedup import (
    cosine_similarity,
    is_semantically_duplicate,
)


class _StubEmbeddings:
    """可控向量桩: 按 text->vector 映射返回向量, 缺省零向量."""

    def __init__(self, vector_map: dict[str, list[float]]) -> None:
        self.vector_map = vector_map

    async def aembed_query(self, text: str) -> list[float]:
        return self.vector_map.get(text, [0.0, 0.0])

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.vector_map.get(t, [0.0, 0.0]) for t in texts]


class _ErrorEmbeddings:
    """恒抛异常的桩, 验证 embedding 失败回退."""

    async def aembed_query(self, text: str) -> list[float]:
        raise RuntimeError("boom")

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("boom")


def test_cosine_similarity_identical_vectors_returns_one() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_returns_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite_returns_minus_one() -> None:
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_returns_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


@pytest.mark.asyncio
async def test_is_duplicate_empty_existing_returns_false() -> None:
    embeddings = _StubEmbeddings({"x": [1.0, 0.0]})
    assert await is_semantically_duplicate("x", [], embeddings, 0.9) is False


@pytest.mark.asyncio
async def test_is_duplicate_above_threshold_returns_true() -> None:
    # 两条不同文本映射到同一向量 -> cosine=1.0 >= 0.90
    embeddings = _StubEmbeddings(
        {
            "偏好暗色主题": [1.0, 0.0],
            "喜欢深色界面": [1.0, 0.0],
        }
    )
    result = await is_semantically_duplicate(
        "喜欢深色界面", ["偏好暗色主题"], embeddings, 0.90
    )
    assert result is True


@pytest.mark.asyncio
async def test_is_duplicate_below_threshold_returns_false() -> None:
    # 正交向量 -> cosine=0.0 < 0.90
    embeddings = _StubEmbeddings(
        {
            "偏好暗色主题": [1.0, 0.0],
            "住在杭州": [0.0, 1.0],
        }
    )
    result = await is_semantically_duplicate(
        "住在杭州", ["偏好暗色主题"], embeddings, 0.90
    )
    assert result is False


@pytest.mark.asyncio
async def test_is_duplicate_takes_max_across_multiple_existing() -> None:
    # 三条已有, 第二条与新条目同向量 -> max=1.0
    embeddings = _StubEmbeddings(
        {
            "新": [1.0, 0.0],
            "A": [0.0, 1.0],
            "B": [1.0, 0.0],
            "C": [-1.0, 0.0],
        }
    )
    result = await is_semantically_duplicate("新", ["A", "B", "C"], embeddings, 0.90)
    assert result is True


@pytest.mark.asyncio
async def test_is_duplicate_embedding_error_returns_false() -> None:
    result = await is_semantically_duplicate("x", ["y"], _ErrorEmbeddings(), 0.90)
    assert result is False
