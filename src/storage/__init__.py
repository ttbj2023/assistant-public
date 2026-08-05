"""存储模块 - 纯异步存储实现

本模块完全采用异步架构, 所有组件都使用 Async* 前缀命名.

**主要组件**:
- AsyncDatabaseManager: 异步数据库管理器
- AsyncTodoDAO: 异步TODO数据访问对象
- AsyncConversationIndexDAO: 异步对话索引数据访问对象
- AsyncPinnedMemoryBlockDAO: 异步统一置顶记忆块数据访问对象
"""

from __future__ import annotations

from .dao.async_conversation_dao import AsyncConversationIndexDAO
from .dao.async_database_manager import (
    AsyncDatabaseManager,
    create_async_conversation_history_db_manager,
    create_async_pinned_memory_db_manager,
    create_async_todo_db_manager,
)
from .dao.async_todo_dao import AsyncTodoDAO
from .langchain_vector_store import LangChainVectorStore, create_langchain_vector_store
from .models import (
    ConversationIndex,
    PinnedMemoryBlock,
    TodoItem,
    TodoPriority,
    TodoStatus,
)
from .service import (
    create_conversation_service,
    create_pinned_memory_block_service,
    create_todo_service,
    create_vector_service,
)

__all__ = [
    "AsyncConversationIndexDAO",
    "AsyncDatabaseManager",
    "AsyncTodoDAO",
    "ConversationIndex",
    "LangChainVectorStore",
    "PinnedMemoryBlock",
    "TodoItem",
    "TodoPriority",
    "TodoStatus",
    "create_async_conversation_history_db_manager",
    "create_async_pinned_memory_db_manager",
    "create_async_todo_db_manager",
    "create_conversation_service",
    "create_langchain_vector_store",
    "create_pinned_memory_block_service",
    "create_todo_service",
    "create_vector_service",
]
