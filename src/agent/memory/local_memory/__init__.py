"""本地记忆模块.

实现了基于 messages 数组架构的本地记忆系统:
- 索引区: 历史对话摘要 (伪对话轮形式)
- 主对话历史: 近期对话内容 (原生 HumanMessage/AIMessage 交替)

主要组件:
- MemoryAssembler: 记忆组装器, 产出 MemoryContext (messages 数组)
- ConversationMemoryCore: 对话记忆核心

注意: 置顶记忆(用户画像)已迁移至 domain_data 子系统, 不再由此模块管理.
"""

from __future__ import annotations

from .assembler import MemoryAssembler, MemoryContext
from .cache import (
    SplittableMemoryCache,
    get_conversation,
    get_pinned_memory,
    get_splittable_memory_cache,
    set_conversation,
    set_pinned_memory,
)
from .core import ConversationMemoryCore

__version__ = "3.0.0"
__all__ = [
    "ConversationMemoryCore",
    "MemoryAssembler",
    "MemoryContext",
    "SplittableMemoryCache",
    "get_conversation",
    "get_pinned_memory",
    "get_splittable_memory_cache",
    "set_conversation",
    "set_pinned_memory",
]
