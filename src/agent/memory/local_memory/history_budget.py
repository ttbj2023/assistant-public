"""主历史字符预算裁剪 - 共享纯函数.

主历史缓存以"滚动有界窗口"形式维护: 每轮把新轮次并入旧窗口, 再裁到
total_char_budget 以内. 写路径(core 滚动更新)与读路径(assembler 冷启动种子化)
共用同一套预算规则, 避免行为分叉.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config.agent_config import AgentConfig
    from src.storage.models.conversation import ConversationIndex

logger = logging.getLogger(__name__)

_DEFAULT_TOTAL_CHAR_BUDGET = 20000


def select_main_history_suffix(
    convs: list[ConversationIndex],
    budget: int,
) -> list[ConversationIndex]:
    """内存二分查找: 选最大后缀使其 content 长度和 <= budget.

    - 输入按 round_number 升序 (DAO 默认行为)
    - 单轮 (最后一轮) 超预算 -> 返回空
    - 否则返回最长的尾部切片

    复杂度: O(N) 预处理 (前缀和) + O(log N) 查找. 不修改输入.
    """
    if not convs or budget <= 0:
        return []

    sizes = [len(c.user_message) + len(c.assistant_response) for c in convs]
    if sizes[-1] > budget:
        return []

    n = len(sizes)
    prefix = [0] * (n + 1)
    for i, s in enumerate(sizes):
        prefix[i + 1] = prefix[i] + s
    total = prefix[n]

    lo, hi, best = 1, n, 1
    while lo <= hi:
        mid = (lo + hi) // 2
        suffix_sum = total - prefix[n - mid]
        if suffix_sum <= budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return convs[n - best :]


def select_index_fine_suffix(
    convs: list[ConversationIndex],
    budget: int,
) -> list[ConversationIndex]:
    """索引区 fine 行 budget 裁剪: 选最大后缀使其渲染字符和 <= budget.

    与 select_main_history_suffix 同构(前缀和 + 二分), 区别是 size 按 fine 行
    渲染字符估算(topic + summary[:80] + 表格行开销), 对齐 format_index_range 输出.
    用于索引区 budget 驱动级联展示: fine 行从后往前填 budget, 溢出部分降级为弧短语.

    - 输入按 round_number 升序
    - 单行超 budget -> 返回空
    - 否则返回最长的尾部切片
    """
    if not convs or budget <= 0:
        return []

    sizes = [_index_fine_row_chars(c) for c in convs]
    if sizes[-1] > budget:
        return []

    n = len(sizes)
    prefix = [0] * (n + 1)
    for i, s in enumerate(sizes):
        prefix[i + 1] = prefix[i] + s
    total = prefix[n]

    lo, hi, best = 1, n, 1
    while lo <= hi:
        mid = (lo + hi) // 2
        suffix_sum = total - prefix[n - mid]
        if suffix_sum <= budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return convs[n - best :]


def _index_fine_row_chars(conv: ConversationIndex) -> int:
    """估算单行 fine 渲染字符(对齐 format_index_range 输出).

    format: "| {round} | {topic} | {summary[:80]} | {time} |" + 换行.
    summary 超 80 截断补 "..."; time(format_date_short) 长度 3-5, 取 4 估算.
    """
    summary = conv.summary or ""
    if len(summary) > 80:
        summary = summary[:80] + "..."
    topic = conv.topic or ""
    return len(f"| {conv.round_number} | {topic} | {summary} | xxxx |") + 1


def resolve_total_char_budget(
    agent_config: AgentConfig | None,
    total_budget: int | None = None,
    fallback: int = _DEFAULT_TOTAL_CHAR_BUDGET,
) -> int:
    """解析主历史总字符预算.

    优先级: 显式参数 > agent_config.memory.total_char_budget > 默认值.
    """
    if isinstance(total_budget, int) and total_budget > 0:
        return total_budget
    if agent_config is not None:
        try:
            cfg_budget = getattr(agent_config.memory, "total_char_budget", None)
            if isinstance(cfg_budget, int) and cfg_budget > 0:
                return cfg_budget
        except Exception as e:
            logger.debug("内存预算配置获取失败, 使用默认值: %s", e)
    return fallback


__all__ = [
    "resolve_total_char_budget",
    "select_index_fine_suffix",
    "select_main_history_suffix",
]
