"""可拆分记忆缓存系统.

提供统一的缓存接口, 支持将记忆内容拆分为3个独立部分:
1. 置顶记忆 (pinned_memory) - 缓存格式化字符串
2. 主历史 (conversation) - 缓存有界的近期 ConversationIndex 滚动窗口
   (冷启动读 DB 种子化, 之后每轮滚动裁剪到 total_char_budget 以内; 读路径命中零 DB)
3. TODO列表 (todolist) - 缓存格式化字符串

设计原则:
- 精确缓存管理: 每个组件独立缓存, 可单独失效
- 统一接口: 3个组件使用相同的缓存底层实现
- 用户-Agent隔离: 基于user_id:thread_id:agent_id确保数据隔离
- 性能优化: 主历史滚动维护有界窗口, 避免无限膨胀与重复 DB 查询
"""

from __future__ import annotations

import logging
from typing import Any

from cachetools import LRUCache

from src.tools.shared.todo_cache_invalidator import set_todo_cache_invalidator

logger = logging.getLogger(__name__)


class SplittableMemoryCache:
    """可拆分的记忆缓存系统.

    提供3部分独立缓存的统一接口,依赖LRU自然淘汰机制.
    缓存键包含agent_id,确保不同Agent之间的数据完全隔离.
    """

    def __init__(
        self,
        max_pinned_memory_size: int = 50,
        max_conversation_size: int = 100,
        max_todolist_size: int = 50,
    ) -> None:
        """初始化可拆分记忆缓存.

        Args:
            max_pinned_memory_size: 置顶记忆缓存最大大小
            max_conversation_size: 对话内容缓存最大大小
            max_todolist_size: TODO列表缓存最大大小

        """
        # 创建三个独立的LRU缓存
        self._pinned_memory_cache: LRUCache = LRUCache(maxsize=max_pinned_memory_size)
        self._conversation_cache: LRUCache = LRUCache(maxsize=max_conversation_size)
        self._todolist_cache: LRUCache = LRUCache(maxsize=max_todolist_size)

        logger.debug(
            "🏗️ SplittableMemoryCache初始化完成: pinned(%s), conversation(%s), todolist(%s)",
            max_pinned_memory_size,
            max_conversation_size,
            max_todolist_size,
        )

    def _build_cache_key(
        self,
        user_id: str,
        thread_id: str,
        component: str,
        *,
        agent_id: str = "",
    ) -> str:
        """构建缓存键, 包含agent_id隔离."""
        if not user_id or not thread_id or not component:
            raise ValueError("用户ID,线程ID和组件名称不能为空")

        component = component.lower().strip()
        agent_part = agent_id.strip() if agent_id else ""
        if agent_part:
            return f"{user_id}:{thread_id}:{agent_part}:memory:{component}"
        return f"{user_id}:{thread_id}:memory:{component}"

    def get_pinned_memory(
        self,
        user_id: str,
        thread_id: str,
        *,
        agent_id: str = "",
    ) -> str | dict[str, Any] | None:
        """获取置顶记忆缓存内容.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID

        Returns:
            缓存的置顶记忆内容(推荐为格式化字符串),如果不存在则返回None

        """
        cache_key = self._build_cache_key(
            user_id,
            thread_id,
            "pinned_memory",
            agent_id=agent_id,
        )
        return self._pinned_memory_cache.get(cache_key)

    def set_pinned_memory(
        self,
        user_id: str,
        thread_id: str,
        content: str | dict[str, Any],
        *,
        agent_id: str = "",
    ) -> None:
        """设置置顶记忆缓存内容.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            content: 置顶记忆内容(推荐为格式化字符串)
            agent_id: Agent ID

        """
        cache_key = self._build_cache_key(
            user_id,
            thread_id,
            "pinned_memory",
            agent_id=agent_id,
        )
        self._pinned_memory_cache[cache_key] = content
        logger.debug("💾 缓存置顶记忆: %s", cache_key)

    def get_conversation(
        self,
        user_id: str,
        thread_id: str,
        *,
        agent_id: str = "",
    ) -> list[Any] | None:
        """获取对话内容缓存 (ConversationIndex 对象列表).

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID

        Returns:
            缓存的 ConversationIndex 列表, 如果不存在则返回 None

        """
        cache_key = self._build_cache_key(
            user_id,
            thread_id,
            "conversation",
            agent_id=agent_id,
        )
        return self._conversation_cache.get(cache_key)

    def set_conversation(
        self,
        user_id: str,
        thread_id: str,
        content: list[Any],
        *,
        agent_id: str = "",
    ) -> None:
        """设置对话内容缓存 (ConversationIndex 对象列表).

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            content: ConversationIndex 对象列表
            agent_id: Agent ID

        """
        cache_key = self._build_cache_key(
            user_id,
            thread_id,
            "conversation",
            agent_id=agent_id,
        )
        self._conversation_cache[cache_key] = content
        logger.debug("缓存对话内容: %s (%d 轮)", cache_key, len(content))

    def get_todolist(
        self,
        user_id: str,
        thread_id: str,
        *,
        agent_id: str = "",
    ) -> str | list[Any] | None:
        """获取TODO列表缓存内容.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID

        Returns:
            缓存的TODO列表内容(推荐为格式化字符串),如果不存在则返回None

        """
        cache_key = self._build_cache_key(
            user_id,
            thread_id,
            "todolist",
            agent_id=agent_id,
        )
        return self._todolist_cache.get(cache_key)

    def set_todolist(
        self,
        user_id: str,
        thread_id: str,
        content: str | list[Any],
        *,
        agent_id: str = "",
    ) -> None:
        """设置TODO列表缓存内容.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            content: TODO列表内容(推荐为格式化字符串)
            agent_id: Agent ID

        """
        cache_key = self._build_cache_key(
            user_id,
            thread_id,
            "todolist",
            agent_id=agent_id,
        )
        self._todolist_cache[cache_key] = content
        logger.debug("💾 缓存TODO列表: %s", cache_key)

    def clear_pinned_memory(
        self,
        user_id: str,
        thread_id: str,
        *,
        agent_id: str = "",
    ) -> None:
        """清理指定用户线程的置顶记忆缓存."""
        cache_key = self._build_cache_key(
            user_id,
            thread_id,
            "pinned_memory",
            agent_id=agent_id,
        )
        self._pinned_memory_cache.pop(cache_key, None)

    def clear_conversation(
        self,
        user_id: str,
        thread_id: str,
        *,
        agent_id: str = "",
    ) -> None:
        """清理指定用户线程的对话内容缓存."""
        cache_key = self._build_cache_key(
            user_id,
            thread_id,
            "conversation",
            agent_id=agent_id,
        )
        self._conversation_cache.pop(cache_key, None)

    def clear_todolist(
        self,
        user_id: str,
        thread_id: str,
        *,
        agent_id: str = "",
    ) -> None:
        """清理指定用户线程的TODO列表缓存."""
        cache_key = self._build_cache_key(
            user_id,
            thread_id,
            "todolist",
            agent_id=agent_id,
        )
        self._todolist_cache.pop(cache_key, None)


# 全局缓存实例
_global_cache: SplittableMemoryCache | None = None


def _create_cache_from_config() -> SplittableMemoryCache:
    """从 CacheConfig 创建缓存实例, 配置读取失败时回退默认值."""
    try:
        from src.config.core_config import get_config

        cache_config = get_config().cache
        return SplittableMemoryCache(
            max_pinned_memory_size=cache_config.pinned_memory_cache_size,
            max_conversation_size=cache_config.conversation_cache_size,
            max_todolist_size=cache_config.todolist_cache_size,
        )
    except Exception as e:
        logger.warning("读取缓存配置失败, 使用默认值: %s", e)
        return SplittableMemoryCache()


def get_splittable_memory_cache() -> SplittableMemoryCache:
    """获取全局可拆分记忆缓存实例.

    Returns:
        全局缓存实例

    """
    global _global_cache
    if _global_cache is None:
        _global_cache = _create_cache_from_config()
        logger.info("🏭 创建全局可拆分记忆缓存实例")
    return _global_cache


def reset_global_cache() -> None:
    """重置全局缓存实例(供测试隔离使用).

    清空单例, 下次访问时按配置重建. 仅用于测试, 生产不应调用.
    """
    global _global_cache
    _global_cache = None


# 便捷函数 - 所有函数均增加agent_id参数确保缓存隔离
def get_pinned_memory(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str = "",
) -> str | dict[str, Any] | None:
    """便捷函数:获取置顶记忆缓存."""
    cache = get_splittable_memory_cache()
    return cache.get_pinned_memory(user_id, thread_id, agent_id=agent_id)


def set_pinned_memory(
    user_id: str,
    thread_id: str,
    content: str | dict[str, Any],
    *,
    agent_id: str = "",
) -> None:
    """便捷函数:设置置顶记忆缓存."""
    cache = get_splittable_memory_cache()
    cache.set_pinned_memory(user_id, thread_id, content, agent_id=agent_id)


def get_conversation(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str = "",
) -> list[Any] | None:
    """便捷函数: 获取对话内容缓存 (ConversationIndex 对象列表)."""
    cache = get_splittable_memory_cache()
    return cache.get_conversation(user_id, thread_id, agent_id=agent_id)


def set_conversation(
    user_id: str,
    thread_id: str,
    content: list[Any],
    *,
    agent_id: str = "",
) -> None:
    """便捷函数: 设置对话内容缓存 (ConversationIndex 对象列表)."""
    cache = get_splittable_memory_cache()
    cache.set_conversation(user_id, thread_id, content, agent_id=agent_id)


def get_todolist(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str = "",
) -> str | list[Any] | None:
    """便捷函数:获取TODO列表缓存."""
    cache = get_splittable_memory_cache()
    return cache.get_todolist(user_id, thread_id, agent_id=agent_id)


def set_todolist(
    user_id: str,
    thread_id: str,
    content: str | list[Any],
    *,
    agent_id: str = "",
) -> None:
    """便捷函数:设置TODO列表缓存."""
    cache = get_splittable_memory_cache()
    cache.set_todolist(user_id, thread_id, content, agent_id=agent_id)


def clear_pinned_memory(user_id: str, thread_id: str, *, agent_id: str = "") -> None:
    """便捷函数:清理置顶记忆缓存."""
    cache = get_splittable_memory_cache()
    cache.clear_pinned_memory(user_id, thread_id, agent_id=agent_id)


def clear_conversation(user_id: str, thread_id: str, *, agent_id: str = "") -> None:
    """便捷函数:清理对话内容缓存."""
    cache = get_splittable_memory_cache()
    cache.clear_conversation(user_id, thread_id, agent_id=agent_id)


def clear_todolist(user_id: str, thread_id: str, *, agent_id: str = "") -> None:
    """便捷函数:清理TODO列表缓存."""
    cache = get_splittable_memory_cache()
    cache.clear_todolist(user_id, thread_id, agent_id=agent_id)


# 注册到中立注册表, 供 tools.internal 解耦调用 (消除 tools.internal -> agent 反向依赖)
set_todo_cache_invalidator(clear_todolist)


# 导出主要接口
__all__ = [
    "SplittableMemoryCache",
    "clear_conversation",
    "clear_pinned_memory",
    "clear_todolist",
    "get_conversation",
    "get_pinned_memory",
    "get_splittable_memory_cache",
    "get_todolist",
    "reset_global_cache",
    "set_conversation",
    "set_pinned_memory",
    "set_todolist",
]
