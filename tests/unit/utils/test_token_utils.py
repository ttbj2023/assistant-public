"""Token估算工具单元测试

专注于验证Token估算算法的正确性和边界条件处理。
纯函数模块，无需Mock任何外部依赖。

**测试职责**: 验证Token估算工具的业务逻辑正确性
**测试范围**: TokenEstimator类和estimate_tokens函数
**Mock策略**: 无需Mock，纯函数测试
"""

import pytest

from src.utils.token_utils import (
    TokenEstimator,
)


class TestTokenEstimator:
    """Token估算器单元测试"""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "text,expected_range",
        [
            ("", 0),
            (None, 0),
            (" ", 1),
            ("\n", 1),
            ("\t", 1),
            ("hello", 2),
            ("你好", 3),
            ("hello 你好", 3),
            ("This is a long English sentence with many words.", 15),
            ("这是一个很长的中文句子，包含很多中文字符。", 32),
            ("Hello world 你好世界", 6),
            ("123456", 3),
            ("!@#$%^", 3),
            ("Python code: def hello():", 10),
            ("URL: https://example.com/path", 12),
            ("emoji 😊 test", 6),
            ("αβγ δε ζ", 3),
            (" русский текст", 5),
        ],
    )
    def test_estimate_tokens_should_return_accurate_estimate_when_various_text_types(
        self, text, expected_range
    ) -> None:
        result = TokenEstimator.estimate_tokens(text)
        assert abs(result - expected_range) <= 2, (
            f"文本 '{text}' 估算Token数 {result} 与预期范围 {expected_range}±2 差异过大"
        )
        assert result >= 0, f"Token估算结果不能为负数: {result}"

    @pytest.mark.unit
    def test_estimate_tokens_chinese_ratio_threshold(self) -> None:
        base_english = "a" * 10

        below_threshold_text = base_english + "你" * 4
        tokens_below = TokenEstimator.estimate_tokens(below_threshold_text)

        above_threshold_text = base_english + "你" * 5
        tokens_above = TokenEstimator.estimate_tokens(above_threshold_text)

        assert tokens_above > tokens_below, (
            f"高于中文阈值应产生更多tokens，实际：低阈值 {tokens_below}，高阈值 {tokens_above}"
        )

    @pytest.mark.unit
    def test_estimate_tokens_should_handle_extreme_lengths_stably(self) -> None:
        long_english = "a" * 10000
        result = TokenEstimator.estimate_tokens(long_english)
        assert result > 0, "长英文文本应产生正数Token"
        assert result < 5000, "长文本Token估算应合理"

        long_chinese = "你" * 5000
        result = TokenEstimator.estimate_tokens(long_chinese)
        assert result > 0, "长中文文本应产生正数Token"
        assert result > 5000, "长中文文本应产生更多Token"

    @pytest.mark.unit
    def test_estimate_tokens_should_handle_edge_cases_gracefully(self) -> None:
        test_cases = [
            "",
            " ",
            "\n\r\t",
            "null",
            "undefined",
        ]

        for test_input in test_cases:
            try:
                result = TokenEstimator.estimate_tokens(test_input)
                assert isinstance(result, int), f"结果应为整数: {result}"
                assert result >= 0, f"结果不应为负数: {result}"
            except Exception as e:
                pytest.fail(f"正常输入 '{test_input}' 不应产生异常: {e}")
