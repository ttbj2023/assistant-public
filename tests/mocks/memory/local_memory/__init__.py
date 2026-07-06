"""Local Memory模块Mock集合

提供针对本地记忆系统中各个组件的专门Mock类，支持精确的单元测试。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from src.core.types import PinnedMemoryUpdateResult
from src.storage.models.conversation import (
    ConversationData,
    ConversationIndex,
)


class ConversationMemoryCoreMocks:
    """ConversationMemoryCore专用Mock集合"""

    def __init__(self):
        """初始化Mock组件"""
        self._setup_storage_mocks()
        self._setup_analyzer_mocks()
        self._setup_vector_mocks()

    def _setup_storage_mocks(self) -> None:
        """设置存储相关Mock"""
        # SQL存储Mock
        self.sql_storage = AsyncMock()
        self.sql_storage.store_conversation_content = AsyncMock()
        self.sql_storage.store_vector_conversation = AsyncMock()

        # DAO Mock
        self.conversation_dao = AsyncMock()
        self.conversation_dao.create_conversation = AsyncMock()
        self.conversation_dao.create_conversation_index = AsyncMock()

    def _setup_analyzer_mocks(self) -> None:
        """设置分析器Mock"""
        self.content_analyzer = AsyncMock()
        self.content_analyzer.analyze_pinned_memory_update = AsyncMock(
            return_value=PinnedMemoryUpdateResult()
        )
        self.content_analyzer.generate_conversation_index = AsyncMock(
            return_value=ConversationIndex(
                user_id="test_user",
                thread_id="test_thread",
                round_number=1,
                user_message="test message",
                assistant_response="test response",
                summary="Test Summary",
                topic="test topic",
                message_count=1,
                token_usage=50,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        )

    def _setup_vector_mocks(self) -> None:
        """设置向量存储Mock"""
        self.vector_store = AsyncMock()
        self.vector_store.add_conversations = AsyncMock()
        self.vector_store.similarity_search = AsyncMock(return_value=[])

    def get_success_scenario(self) -> None:
        """配置成功场景Mock"""
        # 所有操作都成功
        self.sql_storage.store_conversation_content.return_value = None
        self.sql_storage.store_vector_conversation.return_value = None
        self.conversation_dao.create_conversation.return_value = None
        self.conversation_dao.create_conversation_index.return_value = None
        self.content_analyzer.analyze_pinned_memory_update.return_value = (
            PinnedMemoryUpdateResult()
        )
        self.vector_store.add_conversations.return_value = None

    def get_failure_scenario(self, operation: str) -> None:
        """配置失败场景Mock

        Args:
            operation: 失败的操作类型 ('sql', 'vector', 'index', 'pinned')
        """
        if operation == "sql":
            self.sql_storage.store_conversation_content.side_effect = Exception(
                "SQL存储失败"
            )
        elif operation == "vector":
            self.vector_store.add_conversations.side_effect = Exception("向量存储失败")
        elif operation == "index":
            self.content_analyzer.generate_conversation_index.side_effect = Exception(
                "索引生成失败"
            )
        elif operation == "pinned":
            self.content_analyzer.analyze_pinned_memory_update.side_effect = Exception(
                "置顶记忆更新失败"
            )

    def get_mocks(self) -> dict[str, Any]:
        """获取所有Mock对象"""
        return {
            "sql_storage": self.sql_storage,
            "conversation_dao": self.conversation_dao,
            "content_analyzer": self.content_analyzer,
            "vector_store": self.vector_store,
        }


class SplittableMemoryCacheMocks:
    """SplittableMemoryCache专用Mock集合"""

    def __init__(self):
        """初始化Mock组件"""
        self._setup_cache_mocks()
        self._setup_lru_mocks()

    def _setup_cache_mocks(self) -> None:
        """设置缓存相关Mock"""
        self.pinned_memory_cache = AsyncMock()
        self.conversation_cache = AsyncMock()
        self.todolist_cache = AsyncMock()

        # 默认缓存未命中
        self.pinned_memory_cache.get.return_value = None
        self.conversation_cache.get.return_value = None
        self.todolist_cache.get.return_value = None

    def _setup_lru_mocks(self) -> None:
        """设置LRU缓存Mock"""
        self.lru_cache = MagicMock()
        self.lru_cache.get.return_value = None
        self.lru_cache.put.return_value = None
        self.lru_cache.size.return_value = 0

    def get_cache_hit_scenario(self, cache_type: str) -> None:
        """配置缓存命中场景

        Args:
            cache_type: 缓存类型 ('pinned', 'conversation', 'todolist')
        """
        if cache_type == "pinned":
            self.pinned_memory_cache.get.return_value = "cached pinned memory"
        elif cache_type == "conversation":
            self.conversation_cache.get.return_value = "cached conversation"
        elif cache_type == "todolist":
            self.todolist_cache.get.return_value = "cached todos"

    def get_cache_miss_scenario(self) -> None:
        """配置缓存未命中场景"""
        self.pinned_memory_cache.get.return_value = None
        self.conversation_cache.get.return_value = None
        self.todolist_cache.get.return_value = None

    def get_mocks(self) -> dict[str, Any]:
        """获取所有Mock对象"""
        return {
            "pinned_memory_cache": self.pinned_memory_cache,
            "conversation_cache": self.conversation_cache,
            "todolist_cache": self.todolist_cache,
            "lru_cache": self.lru_cache,
        }


class SimplePinnedMemoryManagerMocks:
    """SimplePinnedMemoryManager专用Mock集合"""

    def __init__(self):
        """初始化Mock组件"""
        self._setup_data_manager_mocks()
        self._setup_analyzer_mocks()

    def _setup_data_manager_mocks(self) -> None:
        """设置数据管理器Mock"""
        self.data_manager = MagicMock()
        self.data_manager.memory_dao = AsyncMock()
        self.data_manager.memory_dao.get_pinned_memory = AsyncMock()

        # 默认返回空记忆
        self.data_manager.memory_dao.get_pinned_memory.return_value = {
            "basic_info": "",
            "preferences": "",
        }

    def _setup_analyzer_mocks(self) -> None:
        """设置分析器Mock"""
        self.content_analyzer = AsyncMock()
        self.content_analyzer.analyze_content = AsyncMock(
            return_value={
                "basic_info": "分析后的基础信息",
                "preferences": "分析后的偏好信息",
            }
        )

    def get_normal_memory_scenario(self) -> None:
        """配置正常记忆场景"""
        self.data_manager.memory_dao.get_pinned_memory.return_value = {
            "basic_info": "用户基础信息",
            "preferences": "用户偏好设置",
        }

    def get_empty_memory_scenario(self) -> None:
        """配置空记忆场景"""
        self.data_manager.memory_dao.get_pinned_memory.return_value = {
            "basic_info": "",
            "preferences": "",
        }

    def get_analysis_success_scenario(self) -> None:
        """配置分析成功场景"""
        self.content_analyzer.analyze_content.return_value = {
            "basic_info": "LLM分析的基础信息",
            "preferences": "LLM分析的偏好信息",
        }

    def get_mocks(self) -> dict[str, Any]:
        """获取所有Mock对象"""
        return {
            "data_manager": self.data_manager,
            "content_analyzer": self.content_analyzer,
        }


# 便利函数，用于创建测试数据
def create_mock_conversation_data(
    user_id: str = "test_user",
    thread_id: str = "test_thread",
    user_message: str = "测试用户消息",
    assistant_response: str = "测试助手回复",
    **kwargs,
) -> ConversationData:
    """创建模拟对话数据"""
    from datetime import datetime

    return ConversationData(
        user_id=user_id,
        thread_id=thread_id,
        agent_id=kwargs.get("agent_id", "personal-assistant"),
        user_message=user_message,
        assistant_response=assistant_response,
        round_number=kwargs.get("round_number", 1),
        timestamp=kwargs.get("timestamp", datetime.now()),
    )
