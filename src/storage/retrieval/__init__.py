"""检索模块.

提供两阶段检索架构实现:
1. 第一阶段: SQL精准检索 + 向量库语义搜索, 仅返回轮次号
2. 智能去重: 优先交集 > SQL补充 > 向量库补充
3. 第二阶段: 根据轮次号批量获取完整内容并重排序
"""

from __future__ import annotations

from .smart_deduplication import smart_deduplication_with_scores

__all__ = [
    "smart_deduplication_with_scores",
]
