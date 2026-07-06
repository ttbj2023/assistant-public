"""智能去重模块单元测试.

测试 smart_deduplication_with_scores 的智能去重逻辑.
"""

from __future__ import annotations


class TestSmartDeduplicationWithScores:
    """带分数的智能去重测试类."""

    def test_intersection_prioritized_by_score(self) -> None:
        sql_rounds = [1, 2, 3, 4, 5]
        vector_rounds = [(3, 0.9), (4, 0.8), (5, 0.7), (6, 0.6), (7, 0.5)]

        from src.storage.retrieval.smart_deduplication import (
            smart_deduplication_with_scores,
        )

        result = smart_deduplication_with_scores(sql_rounds, vector_rounds)
        assert result[:3] == [3, 4, 5]

    def test_sql_only_sorted_by_round_desc(self) -> None:
        sql_rounds = [1, 2, 6]
        vector_rounds = [(4, 0.9)]

        from src.storage.retrieval.smart_deduplication import (
            smart_deduplication_with_scores,
        )

        result = smart_deduplication_with_scores(sql_rounds, vector_rounds)
        assert result == [6, 2, 1, 4]

    def test_max_candidates_limit(self) -> None:
        sql_rounds = [1, 2, 3]
        vector_rounds = [(i, 0.5) for i in range(4, 20)]

        from src.storage.retrieval.smart_deduplication import (
            smart_deduplication_with_scores,
        )

        result = smart_deduplication_with_scores(
            sql_rounds, vector_rounds, max_candidates=5
        )
        assert len(result) == 5

    def test_empty_inputs(self) -> None:
        from src.storage.retrieval.smart_deduplication import (
            smart_deduplication_with_scores,
        )

        assert smart_deduplication_with_scores([], []) == []
        assert smart_deduplication_with_scores([1, 2], []) == [2, 1]
        assert smart_deduplication_with_scores([], [(1, 0.5)]) == [1]

    def test_full_overlap(self) -> None:
        sql_rounds = [1, 2, 3]
        vector_rounds = [(3, 0.9), (2, 0.8), (1, 0.7)]

        from src.storage.retrieval.smart_deduplication import (
            smart_deduplication_with_scores,
        )

        result = smart_deduplication_with_scores(sql_rounds, vector_rounds)
        assert result == [3, 2, 1]

    def test_exception_fallback(self) -> None:
        """异常回退：SQL排序失败时回退到按输入顺序简单合并."""
        # sql_only_rounds 含 None+int 使 sorted() 触发 TypeError,
        # 验证 except 块回退为 dict.fromkeys 去重合并
        sql_rounds = [1, 2, None]
        vector_rounds = [(3, 0.5)]

        from src.storage.retrieval.smart_deduplication import (
            smart_deduplication_with_scores,
        )

        result = smart_deduplication_with_scores(sql_rounds, vector_rounds)
        # 回退逻辑: dict.fromkeys([1, 2, None, 3]) → [1, 2, None, 3]
        assert result == [1, 2, None, 3]
