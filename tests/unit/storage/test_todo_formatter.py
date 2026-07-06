"""TODO列表格式化器单元测试.

覆盖 TodoFormatter.format_todolist 的核心逻辑:
- 空列表返回空字符串
- 基本格式化
- priority/status枚举和字符串处理
- include_section_title选项
- 空title跳过
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.storage.formatters.todo_formatter import TodoFormatter


@pytest.fixture
def formatter() -> TodoFormatter:
    return TodoFormatter()


class TestFormatTodolist:
    """format_todolist 测试."""

    @pytest.mark.asyncio
    async def test_none_todos(self, formatter: TodoFormatter) -> None:
        result = await formatter.format_todolist(None)
        assert result == ""

    @pytest.mark.asyncio
    async def test_empty_todos(self, formatter: TodoFormatter) -> None:
        result = await formatter.format_todolist([])
        assert result == ""

    @pytest.mark.asyncio
    async def test_basic_formatting(self, formatter: TodoFormatter) -> None:
        with (
            patch(
                "src.storage.formatters.todo_formatter.validate_format_template",
                return_value="markdown",
            ),
            patch(
                "src.storage.formatters.todo_formatter.format_due_date_short",
                return_value="",
            ),
            patch(
                "src.storage.formatters.todo_formatter.create_todo_item_text",
                return_value="[中] 任务标题",
            ),
        ):
            todos = [{"title": "任务标题", "priority": "medium", "status": "pending"}]
            result = await formatter.format_todolist(todos)
            assert "- [中] 任务标题" in result

    @pytest.mark.asyncio
    async def test_skip_empty_title(self, formatter: TodoFormatter) -> None:
        with patch(
            "src.storage.formatters.todo_formatter.validate_format_template",
            return_value="markdown",
        ):
            todos = [{"title": "", "priority": "medium", "status": "pending"}]
            result = await formatter.format_todolist(todos)
            assert result == ""

    @pytest.mark.asyncio
    async def test_skip_whitespace_title(self, formatter: TodoFormatter) -> None:
        with patch(
            "src.storage.formatters.todo_formatter.validate_format_template",
            return_value="markdown",
        ):
            todos = [{"title": "   ", "priority": "medium", "status": "pending"}]
            result = await formatter.format_todolist(todos)
            assert result == ""

    @pytest.mark.asyncio
    async def test_include_section_title(self, formatter: TodoFormatter) -> None:
        with (
            patch(
                "src.storage.formatters.todo_formatter.validate_format_template",
                return_value="markdown",
            ),
            patch(
                "src.storage.formatters.todo_formatter.format_due_date_short",
                return_value="",
            ),
            patch(
                "src.storage.formatters.todo_formatter.create_todo_item_text",
                return_value="[中] 任务",
            ),
        ):
            todos = [{"title": "任务", "priority": "medium", "status": "pending"}]
            result = await formatter.format_todolist(todos, include_section_title=True)
            assert "### " in result

    @pytest.mark.asyncio
    async def test_enum_priority(self, formatter: TodoFormatter) -> None:
        from enum import StrEnum

        class Priority(StrEnum):
            HIGH = "high"

        with (
            patch(
                "src.storage.formatters.todo_formatter.validate_format_template",
                return_value="markdown",
            ),
            patch(
                "src.storage.formatters.todo_formatter.format_due_date_short",
                return_value="",
            ),
            patch(
                "src.storage.formatters.todo_formatter.create_todo_item_text",
                return_value="[高] 任务",
            ),
        ):
            todos = [{"title": "任务", "priority": Priority.HIGH, "status": "pending"}]
            result = await formatter.format_todolist(todos)
            assert "- [高] 任务" in result

    @pytest.mark.asyncio
    async def test_multiple_todos(self, formatter: TodoFormatter) -> None:
        with (
            patch(
                "src.storage.formatters.todo_formatter.validate_format_template",
                return_value="markdown",
            ),
            patch(
                "src.storage.formatters.todo_formatter.format_due_date_short",
                return_value="",
            ),
            patch(
                "src.storage.formatters.todo_formatter.create_todo_item_text",
                side_effect=["[高] 任务A", "[低] 任务B"],
            ),
        ):
            todos = [
                {"title": "任务A", "priority": "high", "status": "pending"},
                {"title": "任务B", "priority": "low", "status": "done"},
            ]
            result = await formatter.format_todolist(todos)
            assert "任务A" in result
            assert "任务B" in result
