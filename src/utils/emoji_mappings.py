"""Emoji映射工具 - 提供标准化的Emoji映射功能.

专门用于各种状态的Emoji展示,简化记忆系统格式化逻辑.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 标准Emoji映射表
PRIORITY_EMOJI = {
    "high": "🔴",
    "low": "🟢",
    "urgent": "🟣",
    "medium": "🟡",
}

TODO_STATUS_LABEL = {
    "pending": "待办",
    "in_progress": "进行中",
    "completed": "已完成",
    "cancelled": "已取消",
}


def get_emoji_by_priority(priority: str) -> str:
    """根据优先级获取对应的emoji.

    Args:
        priority: 优先级字符串

    Returns:
        对应的emoji字符,未找到时返回默认值

    """
    return PRIORITY_EMOJI.get(priority, "⚪")


def get_todo_status_label(status: str) -> str:
    """获取TODO状态标签.

    Args:
        status: 状态字符串

    Returns:
        对应的状态标签,未找到时返回原值

    """
    return TODO_STATUS_LABEL.get(status, status)


def create_todo_item_text(
    title: str,
    priority: str = "medium",
    status: str = "pending",
    due_date: str = "",
    todo_id: int | None = None,
) -> str:
    """创建TODO项目的文本表示.

    Args:
        title: TODO标题
        priority: 优先级
        status: 状态
        due_date: 截止日期
        todo_id: TODO的数据库ID, 用于Agent直接引用避免额外list调用

    Returns:
        格式化的TODO项目文本

    """
    if not title or not title.strip():
        return ""

    priority_emoji = get_emoji_by_priority(priority)
    status_label = get_todo_status_label(status)

    # ID前缀: 方便Agent直接引用进行update/delete操作
    id_prefix = f"[#{todo_id}] " if todo_id is not None else ""

    # 基础格式: [ID] + 优先级emoji + 标题 + 状态标签
    item_text = f"{id_prefix}{priority_emoji} {title} ({status_label})"

    # 添加截止日期
    if due_date and due_date.strip():
        item_text += f" - 截止: {due_date}"

    return item_text


__all__ = [
    "PRIORITY_EMOJI",
    "TODO_STATUS_LABEL",
    "create_todo_item_text",
    "get_emoji_by_priority",
    "get_todo_status_label",
]
