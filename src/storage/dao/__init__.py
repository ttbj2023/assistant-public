"""异步数据访问对象(DAO)模块.

提供对数据库的标准化异步访问接口, 包含通用CRUD操作和专业化的数据访问逻辑.

**主要组件**:
- AsyncDatabaseOperations: 通用数据库操作组件(组合模式)
- AsyncDatabaseManager: 异步数据库管理器
- AsyncTodoDAO: 异步TODO数据访问
- AsyncConversationIndexDAO: 异步对话索引数据访问
- AsyncSimplePinnedMemoryDAO: 异步置顶记忆数据访问
"""

from __future__ import annotations

from .async_conversation_dao import AsyncConversationIndexDAO
from .async_database_manager import (
    AsyncDatabaseManager,
    create_async_channel_config_db_manager,
    create_async_conversation_history_db_manager,
    create_async_pinned_memory_db_manager,
    create_async_scheduled_message_db_manager,
    create_async_todo_db_manager,
    create_async_usage_db_manager,
)
from .async_simple_pinned_memory_dao import AsyncSimplePinnedMemoryDAO
from .async_todo_dao import AsyncTodoDAO
from .async_usage_dao import AsyncUsageDAO
from .async_user_channel_config_dao import AsyncUserChannelConfigDAO
from .database_operations import AsyncDatabaseOperations

__all__ = [
    "AsyncConversationIndexDAO",
    "AsyncDatabaseManager",
    "AsyncDatabaseOperations",
    "AsyncSimplePinnedMemoryDAO",
    "AsyncTodoDAO",
    "AsyncUsageDAO",
    "AsyncUserChannelConfigDAO",
    "create_async_channel_config_db_manager",
    "create_async_conversation_history_db_manager",
    "create_async_pinned_memory_db_manager",
    "create_async_scheduled_message_db_manager",
    "create_async_todo_db_manager",
    "create_async_usage_db_manager",
]
