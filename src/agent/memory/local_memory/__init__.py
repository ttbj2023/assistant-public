"""本地记忆模块.

实现了基于 messages 数组架构的本地记忆系统:
- 置顶记忆: 统一单一块, 主模型每轮全文覆写 (注入 system_prompt_extension)
- 索引区: 历史对话摘要 (伪对话轮形式)
- 主对话历史: 近期对话内容 (原生 HumanMessage/AIMessage 交替)

主要组件:
- MemoryAssembler: 记忆组装器, 产出 MemoryContext (messages 数组 + 扩展)
- PinnedMemoryService: 置顶记忆服务 (主模型覆写)
- ConversationMemoryCore: 对话记忆核心
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
from .pinned_memory_service import PinnedMemoryService

__version__ = "2.0.0"
__all__ = [
    "ConversationMemoryCore",
    "MemoryAssembler",
    "MemoryContext",
    "PinnedMemoryService",
    "SplittableMemoryCache",
    "get_conversation",
    "get_pinned_memory",
    "get_splittable_memory_cache",
    "set_conversation",
    "set_pinned_memory",
]
