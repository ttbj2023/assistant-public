"""TodoItem 数据模型单元测试.

验证字段验证器 validate_title / validate_tags 的正确行为.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.storage.models.todo import TodoItem


class TestTodoItemTitleValidation:
    """TodoItem.title 字段验证器测试."""

    def test_validate_title_empty_raises(self) -> None:
        """空标题应触发 ValueError → ValidationError."""
        with pytest.raises(ValidationError, match="任务标题不能为空"):
            TodoItem(title="", user_id="u", thread_id="t")

    def test_validate_title_too_long_raises(self) -> None:
        """标题超过 200 字符应触发 ValueError → ValidationError."""
        with pytest.raises(ValidationError, match="任务标题不能超过200个字符"):
            TodoItem(title="x" * 201, user_id="u", thread_id="t")

    def test_validate_title_valid_returns_stripped(self) -> None:
        """合法标题返回裁剪后的值."""
        item = TodoItem(title="  测试任务  ", user_id="u", thread_id="t")
        assert item.title == "测试任务"


class TestTodoItemTagsValidation:
    """TodoItem.tags 字段验证器测试."""

    def test_validate_tags_none_returns_none(self) -> None:
        """tags=None 应返回 None."""
        item = TodoItem(title="test", tags=None, user_id="u", thread_id="t")
        assert item.tags is None

    def test_validate_tags_valid_string(self) -> None:
        """合法 tags 字符串应被清理和重组."""
        item = TodoItem(title="test", tags="  a , b , c  ", user_id="u", thread_id="t")
        assert item.tags == "a,b,c"

    def test_validate_tags_too_many_raises(self) -> None:
        """tags 超过 10 个应触发 ValueError → ValidationError."""
        tags = ",".join(f"tag{i}" for i in range(11))
        with pytest.raises(ValidationError, match="任务标签不能超过10个"):
            TodoItem(title="test", tags=tags, user_id="u", thread_id="t")

    def test_validate_tags_skips_empty_segments(self) -> None:
        """空标签段应被过滤."""
        item = TodoItem(title="test", tags="a,,b,,,c", user_id="u", thread_id="t")
        assert item.tags == "a,b,c"
