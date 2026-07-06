"""智能去重逻辑模块.

实现两阶段检索中的智能去重策略:
1. 交集优先 (SQL ∩ 向量库结果的交集) - 最高优先级
2. SQL补充 (SQL结果中不在交集的部分) - 第二优先级
3. 向量库补充 (向量库结果中不在前两部分的内容) - 最低优先级
4. 总数控制: 如果结果过多, 优先保留交集和SQL部分
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def smart_deduplication_with_scores(
    sql_rounds: list[int],
    vector_rounds_with_scores: list[tuple[int, float]],
    max_candidates: int = 30,
) -> list[int]:
    """基于向量得分的智能去重合并SQL和向量库检索结果.

    根据优先级合并两个轮次号列表, 使用向量得分进行排序:
    - 交集优先: 既在SQL结果中又在向量库结果中的轮次号, 按向量得分降序
    - SQL补充: 只在SQL结果中的轮次号, 按轮次号降序 (最新优先)
    - 向量库补充: 只在向量库结果中的轮次号, 按向量得分降序

    Args:
        sql_rounds: SQL精准检索返回的轮次号列表
        vector_rounds_with_scores: 向量库语义检索返回的(轮次号, 得分)列表
        max_candidates: 最大候选数量限制 (默认30)

    Returns:
        去重并按优先级排序的轮次号列表

    """
    try:
        sql_set = set(sql_rounds)
        vector_dict = dict(vector_rounds_with_scores)

        intersection_rounds = [r for r in sql_set if r in vector_dict]
        intersection_scores = [(r, vector_dict[r]) for r in intersection_rounds]
        intersection_scores.sort(key=lambda x: x[1], reverse=True)
        intersection_sorted = [r for r, _ in intersection_scores]

        logger.debug(
            "交集: %d 个轮次号, 平均得分: %.3f",
            len(intersection_sorted),
            sum(s for _, s in intersection_scores) / len(intersection_scores)
            if intersection_scores
            else 0,
        )

        sql_only_rounds = sorted(
            [r for r in sql_rounds if r not in vector_dict],
            reverse=True,
        )
        logger.debug("SQL独有: %d 个", len(sql_only_rounds))

        vector_only_scores = [
            (r, s) for r, s in vector_rounds_with_scores if r not in sql_set
        ]
        vector_only_scores.sort(key=lambda x: x[1], reverse=True)
        vector_only_sorted = [r for r, _ in vector_only_scores]

        logger.debug("向量库独有: %d 个", len(vector_only_sorted))

        final_rounds: list[int] = []
        final_rounds.extend(intersection_sorted)
        if len(final_rounds) < max_candidates:
            remaining = max_candidates - len(final_rounds)
            final_rounds.extend(sql_only_rounds[:remaining])
        if len(final_rounds) < max_candidates:
            remaining = max_candidates - len(final_rounds)
            final_rounds.extend(vector_only_sorted[:remaining])

        final_rounds = final_rounds[:max_candidates]

        logger.info(
            "智能去重完成: 总候选 %d (交集:%d, SQL:%d, 向量:%d)",
            len(final_rounds),
            len(intersection_sorted),
            min(
                len(sql_only_rounds),
                max(0, max_candidates - len(intersection_sorted)),
            ),
            min(
                len(vector_only_sorted),
                max(
                    0,
                    max_candidates - len(intersection_sorted) - len(sql_only_rounds),
                ),
            ),
        )

        return final_rounds

    except Exception as e:
        logger.error("智能去重失败: %s", e)
        vector_rounds = [r for r, _ in vector_rounds_with_scores]
        return list(dict.fromkeys(sql_rounds + vector_rounds))[:max_candidates]


__all__ = [
    "smart_deduplication_with_scores",
]
