"""Simple 模式记忆子系统.

轻量记忆架构: 仅管理 system_prompt_extension(长期记忆) + 当前轮记录.
对话历史由前端透传给 LLM, 不由本子系统组装.

核心组件:
- SimpleMemoryCore: 对话完成后的统一触发点(存当前轮 + 触发 Stage 1 提取)
- SimpleMemoryService: fire-and-forget Stage 1 提取编排 + RMW 锁
- SimpleMemoryManager: preferences/insights 两字段读写 + 三层去重
"""

from __future__ import annotations

from .core import SimpleMemoryCore
from .manager import SimpleMemoryManager
from .service import SimpleMemoryService

__all__ = ["SimpleMemoryCore", "SimpleMemoryManager", "SimpleMemoryService"]
