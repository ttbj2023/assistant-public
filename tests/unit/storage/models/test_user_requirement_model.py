"""UserRequirement 数据模型单元测试.

验证字段验证器 validate_content 的正确行为.
"""

from __future__ import annotations

from src.storage.models.user_requirement import UserRequirement


class TestUserRequirementContentValidation:
    """UserRequirement.content 字段验证器测试."""

    def test_content_empty_returns_empty_string(self) -> None:
        """空内容应返回空字符串."""
        model = UserRequirement(user_id="u", thread_id="t", content="")
        assert model.content == ""

    def test_content_non_empty_returns_stripped(self) -> None:
        """非空内容返回裁剪后的值 (model_config 已 str_strip_whitespace, 验证器仍做 v.strip())."""
        model = UserRequirement(user_id="u", thread_id="t", content="  需要简洁回复  ")
        # str_strip_whitespace 已在验证前裁剪, 内容应为 "需要简洁回复"
        assert model.content == "需要简洁回复"
