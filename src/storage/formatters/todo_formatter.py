"""TODO列表格式化器.

将TODO格式化逻辑从应用层下沉到存储层,提供统一的字符串格式输出.
"""

from __future__ import annotations

import logging
from typing import Any

from src.utils import (
    create_todo_item_text,
    format_due_date_short,
    validate_format_template,
)

logger = logging.getLogger(__name__)


class TodoFormatter:
    """TODO列表格式化器."""

    def __init__(self) -> None:
        logger.debug("🔧 初始化TodoFormatter")

    async def format_todolist(
        self,
        todos: list[dict[str, Any]] | None,
        *,
        include_section_title: bool = False,
        format_template: str = "markdown",
    ) -> str:
        """将TODO列表格式化为字符串.

        Args:
            todos: TODO字典列表
            include_section_title: 是否包含标题
            format_template: 格式化模板(目前仅支持 markdown)

        Returns:
            格式化后的TODO字符串

        """
        format_template = validate_format_template(format_template)

        if not todos:
            return ""

        lines: list[str] = []
        for todo in todos:
            title = str(todo.get("title", "")).strip()
            if not title:
                continue

            priority = str(todo.get("priority", "medium")).lower().strip()

            status = str(todo.get("status", "")).lower().strip()

            due_date = todo.get("due_date")
            due_str = format_due_date_short(due_date)

            # 提取数据库ID, 传递给格式化函数
            item_id = todo.get("id")
            if item_id is not None:
                item_id = int(item_id)

            # 使用统一的工具函数创建TODO项目文本
            todo_text = create_todo_item_text(
                title,
                priority,
                status,
                due_str,
                todo_id=item_id,
            )
            lines.append(f"- {todo_text}")

        if not lines:
            return ""

        if include_section_title:
            return "### 📋 TODO列表\n\n" + "\n".join(lines)
        return "\n".join(lines)


def create_todo_formatter() -> TodoFormatter:
    """创建TodoFormatter实例."""
    return TodoFormatter()


__all__ = ["TodoFormatter", "create_todo_formatter"]
