"""PinnedMemoryBlock 数据模型单元测试.

验证字段验证器 validate_content 的正确行为.
"""

from __future__ import annotations

from src.storage.models.pinned_memory_block import PinnedMemoryBlock


class TestPinnedMemoryBlockContentValidation:
    """PinnedMemoryBlock.content 字段验证器测试."""

    def test_content_empty_returns_empty_string(self) -> None:
        """空内容应返回空字符串."""
        model = PinnedMemoryBlock(user_id="u", thread_id="t", content="")
        assert model.content == ""

    def test_content_non_empty_returns_stripped(self) -> None:
        """非空内容返回裁剪后的值."""
        model = PinnedMemoryBlock(
            user_id="u",
            thread_id="t",
            content="  用户位于湖北  ",
        )
        assert model.content == "用户位于湖北"
