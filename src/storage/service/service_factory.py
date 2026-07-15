"""Service层工厂函数模块.

集中管理所有Service实例的创建逻辑, 统一签名:
    async def create_xxx_service(user_id, thread_id, *, agent_id) -> XxxService

外部调用方应通过 src.storage.service 包导入:
    from src.storage.service import create_todo_service

缓存策略:
    无状态Service每次直接创建 (底层AsyncDatabaseManager由Layer 1全局复用).
    仅VectorService按(user_id, thread_id, agent_id)缓存 (ChromaDB客户端/线程池重量级).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_vector_cache: dict[str, Any] = {}


def _service_cache_key(
    service_type: str, user_id: str, thread_id: str, agent_id: str
) -> str:
    return f"{service_type}:{user_id}:{thread_id}:{agent_id}"


async def create_conversation_service(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
) -> Any:
    """创建对话服务实例 (底层Engine全局复用).

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        对话服务实例

    """
    from src.storage.dao.async_database_manager import (
        create_async_conversation_history_db_manager,
    )

    from .conversation_service import ConversationService

    db_manager = await create_async_conversation_history_db_manager(
        user_id,
        thread_id,
        agent_id=agent_id,
    )
    return ConversationService(db_manager.session_factory)


async def create_todo_service(user_id: str, thread_id: str, *, agent_id: str) -> Any:
    """创建TODO服务实例 (底层Engine全局复用).

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        TODO服务实例

    """
    from src.storage.dao.async_database_manager import create_async_todo_db_manager

    from .todo_service import TodoService

    db_manager = await create_async_todo_db_manager(
        user_id,
        thread_id,
        agent_id=agent_id,
    )
    return TodoService(db_manager.session_factory)


async def create_memory_service(user_id: str, thread_id: str, *, agent_id: str) -> Any:
    """创建记忆服务实例 (底层Engine全局复用).

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        记忆服务实例

    """
    from src.storage.dao.async_database_manager import (
        create_async_pinned_memory_db_manager,
    )

    from .memory_service import MemoryService

    db_manager = await create_async_pinned_memory_db_manager(
        user_id,
        thread_id,
        agent_id=agent_id,
    )
    return MemoryService(db_manager.session_factory)


async def create_pinned_memory_block_service(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
) -> Any:
    """创建统一置顶记忆单一块服务实例 (与 SimplePinnedMemory 共享 pinned_memory.db).

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        PinnedMemoryBlockService 实例

    """
    from src.storage.dao.async_database_manager import (
        create_async_pinned_memory_db_manager,
    )

    from .pinned_memory_block_service import PinnedMemoryBlockService

    db_manager = await create_async_pinned_memory_db_manager(
        user_id,
        thread_id,
        agent_id=agent_id,
    )
    return PinnedMemoryBlockService(db_manager.session_factory)


def create_vector_service(user_id: str, thread_id: str, *, agent_id: str) -> Any:
    """创建向量存储服务实例 (带缓存, 复用ChromaDB客户端).

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        向量存储服务实例

    """
    cache_key = _service_cache_key("vector", user_id, thread_id, agent_id)
    if cache_key in _vector_cache:
        return _vector_cache[cache_key]

    from src.storage.langchain_vector_store import create_langchain_vector_store

    from .vector_service import VectorService

    vector_store = create_langchain_vector_store(
        user_id=user_id,
        thread_id=thread_id,
        agent_id=agent_id,
    )
    service = VectorService(
        user_id=user_id,
        thread_id=thread_id,
        vector_store=vector_store,
    )
    _vector_cache[cache_key] = service
    return service


async def create_retrieval_service(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
    enable_sql_search: bool = True,
    enable_vector_search: bool = True,
    max_results: int = 3,
) -> Any:
    """创建检索服务实例.

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID
        enable_sql_search: 是否启用SQL搜索
        enable_vector_search: 是否启用向量搜索
        max_results: 默认最大结果数量

    Returns:
        检索服务实例

    """
    from .retrieval_service import DualStageRetrievalService

    conversation_service = await create_conversation_service(
        user_id,
        thread_id,
        agent_id=agent_id,
    )

    try:
        from src.config.inference_config import get_config

        inference_config = get_config()

        if not inference_config.embeddings.enabled:
            logger.info(
                "📋 嵌入模型已禁用(inference.embeddings.enabled=false),"
                "跳过向量服务创建,使用纯SQL检索模式",
            )
            vector_service = None
            enable_vector_search = False
        else:
            vector_service = create_vector_service(
                user_id,
                thread_id,
                agent_id=agent_id,
            )
    except Exception as e:
        logger.warning("⚠️ 检查嵌入模型配置失败,尝试创建向量服务: %s", e)
        vector_service = create_vector_service(user_id, thread_id, agent_id=agent_id)

    return DualStageRetrievalService(
        conversation_service=conversation_service,
        vector_service=vector_service,
        user_id=user_id,
        thread_id=thread_id,
        enable_sql_search=enable_sql_search,
        enable_vector_search=enable_vector_search,
        max_results=max_results,
    )


async def create_health_service(user_id: str, thread_id: str, *, agent_id: str) -> Any:
    """创建健康数据服务实例.

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        健康数据服务实例

    """
    from .health_service import get_health_service

    return await get_health_service(user_id, thread_id, agent_id=agent_id)


async def create_scheduled_message_service(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
    **config_kwargs: Any,
) -> Any:
    """创建定时消息服务实例.

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID
        **config_kwargs: 传递给ScheduledMessageService的配置参数

    Returns:
        定时消息服务实例

    """
    from .scheduled_message_service import get_scheduled_message_service

    return await get_scheduled_message_service(
        user_id,
        thread_id,
        agent_id,
        **config_kwargs,
    )


async def create_usage_service(user_id: str) -> Any:
    """创建用户级用量统计服务实例.

    Args:
        user_id: 用户ID

    Returns:
        用量统计服务实例

    """
    from src.storage.dao.async_database_manager import create_async_usage_db_manager

    from .usage_service import UsageService

    db_manager = await create_async_usage_db_manager(user_id)
    return UsageService(db_manager.session_factory)


def clear_vector_cache() -> None:
    """清空向量服务缓存 (应用关闭时调用)."""
    _vector_cache.clear()
    logger.info("已清空向量服务缓存")
