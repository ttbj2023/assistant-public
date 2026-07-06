"""本地记忆模块.

实现了基于 messages 数组架构的本地记忆系统:
- 置顶记忆: 用户基础信息, 偏好, 重要信息 (注入 system_prompt_extension)
- 索引区: 历史对话摘要 (伪对话轮形式)
- 主对话历史: 近期对话内容 (原生 HumanMessage/AIMessage 交替)
- TODO列表: 待办事项 (可选, 由 include_todo_in_context 配置控制)

主要组件:
- MemoryAssembler: 记忆组装器, 产出 MemoryContext (messages 数组 + 扩展)
- PinnedMemoryManager: 置顶记忆管理器
- ConversationMemoryCore: 对话记忆核心
- MemoryCache: 可拆分缓存系统

使用示例:
    from src.agent.memory.local_memory import MemoryAssembler
    from src.config.agent_config import AgentConfig

    agent_config = AgentConfig()
    assembler = MemoryAssembler(agent_id="personal-assistant", agent_config=agent_config, user_id=user_id, thread_id=thread_id)
    context = await assembler.assemble_memory_context(user_id, thread_id)
"""

from __future__ import annotations

from .assembler import MemoryAssembler, MemoryContext
from .cache import (
    SplittableMemoryCache,
    get_conversation,
    get_pinned_memory,
    get_splittable_memory_cache,
    get_todolist,
    set_conversation,
    set_pinned_memory,
    set_todolist,
)
from .core import ConversationMemoryCore
from .pinned_memory import SimplePinnedMemoryManager
from .pinned_memory_service import PinnedMemoryService

__version__ = "2.0.0"
__all__ = [
    "ConversationMemoryCore",
    "MemoryAssembler",
    "MemoryContext",
    "PinnedMemoryService",
    "SimplePinnedMemoryManager",
    "SplittableMemoryCache",
    "get_conversation",
    "get_pinned_memory",
    "get_splittable_memory_cache",
    "get_todolist",
    "set_conversation",
    "set_pinned_memory",
    "set_todolist",
]
