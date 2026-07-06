"""history_budget 共享纯函数单元测试.

测试范围:
- select_main_history_suffix 内存二分查找(写路径滚动裁剪 + 读路径冷启动种子化共用)
- resolve_total_char_budget 预算解析优先级
"""

from __future__ import annotations

from unittest.mock import Mock

from src.agent.memory.local_memory.history_budget import (
    _DEFAULT_TOTAL_CHAR_BUDGET,
    resolve_total_char_budget,
    select_main_history_suffix,
)
from src.storage.models.conversation import ConversationIndex


def _make_conv(round_number: int, user: str, asst: str) -> ConversationIndex:
    """构造 ConversationIndex (round_number + 内容)."""
    return ConversationIndex(
        round_number=round_number,
        user_message=user,
        assistant_response=asst,
    )


class TestSelectMainHistorySuffix:
    """select_main_history_suffix 内存二分查找测试."""

    def test_empty_convs_returns_empty(self) -> None:
        """空列表应返回空."""
        assert select_main_history_suffix([], 1000) == []

    def test_zero_budget_returns_empty(self) -> None:
        """0 或负预算应返回空."""
        convs = [_make_conv(1, "u", "a")]
        assert select_main_history_suffix(convs, 0) == []
        assert select_main_history_suffix(convs, -1) == []

    def test_single_round_fits(self) -> None:
        """单轮且在预算内应返回该轮."""
        convs = [_make_conv(1, "hello", "world")]
        result = select_main_history_suffix(convs, 100)
        assert result == convs

    def test_single_round_over_budget_returns_empty(self) -> None:
        """单轮 (最后一轮) 超预算应返回空."""
        convs = [_make_conv(1, "hello", "world")]
        budget = len("hello") + len("world") - 1
        assert select_main_history_suffix(convs, budget) == []

    def test_returns_longest_suffix_within_budget(self) -> None:
        """应返回最大尾部切片使其 content 长度和 <= budget."""
        convs = [
            _make_conv(1, "aaaa", "bbbb"),
            _make_conv(2, "cccc", "dddd"),
            _make_conv(3, "ee", "ff"),
        ]
        result = select_main_history_suffix(convs, 12)
        assert len(result) == 2
        assert result[0].round_number == 2
        assert result[1].round_number == 3

    def test_all_rounds_fit_returns_full_list(self) -> None:
        """所有轮次都在预算内应返回完整列表."""
        convs = [
            _make_conv(1, "a", "b"),
            _make_conv(2, "c", "d"),
        ]
        result = select_main_history_suffix(convs, 1000)
        assert result == convs

    def test_does_not_mutate_input(self) -> None:
        """应不修改输入列表."""
        convs = [_make_conv(1, "u", "a"), _make_conv(2, "u", "a")]
        original = list(convs)
        select_main_history_suffix(convs, 100)
        assert convs == original

    def test_preserves_input_order(self) -> None:
        """输出顺序应与输入一致."""
        convs = [
            _make_conv(3, "a", "b"),
            _make_conv(1, "c", "d"),
        ]
        result = select_main_history_suffix(convs, 1000)
        assert [c.round_number for c in result] == [3, 1]

    def test_rolling_trim_drops_oldest(self) -> None:
        """滚动裁剪场景: 追加新轮后超预算应丢弃最老的轮次(写路径语义)."""
        # 预算仅容 2 轮 (每轮 4 字符)
        budget = 8
        window = [
            _make_conv(1, "ab", "cd"),
            _make_conv(2, "ef", "gh"),
        ]
        rolled = select_main_history_suffix(
            [*window, _make_conv(3, "ij", "kl")], budget
        )
        assert [c.round_number for c in rolled] == [2, 3]


class TestResolveTotalCharBudget:
    """resolve_total_char_budget 预算解析测试."""

    def test_explicit_param_takes_priority(self) -> None:
        """显式参数优先级最高."""
        config = Mock()
        config.memory = Mock()
        config.memory.total_char_budget = 9999
        assert resolve_total_char_budget(config, total_budget=500) == 500

    def test_config_value_when_no_param(self) -> None:
        """无显式参数时取配置值."""
        config = Mock()
        config.memory = Mock()
        config.memory.total_char_budget = 30000
        assert resolve_total_char_budget(config) == 30000

    def test_fallback_when_no_config(self) -> None:
        """无配置时回退默认值."""
        assert resolve_total_char_budget(None) == _DEFAULT_TOTAL_CHAR_BUDGET
        assert resolve_total_char_budget(None, fallback=1234) == 1234

    def test_invalid_param_falls_through(self) -> None:
        """非正整数参数应被忽略, 走配置/默认."""
        config = Mock()
        config.memory = Mock()
        config.memory.total_char_budget = 7777
        assert resolve_total_char_budget(config, total_budget=0) == 7777
        assert resolve_total_char_budget(config, total_budget=-5) == 7777
