"""Simple 模式记忆子系统.

轻量记忆架构: 仅管理 system_prompt_extension(长期记忆) + 当前轮记录.
对话历史由前端透传给 LLM, 不由本子系统组装.

核心组件:
- SimpleMemoryCore: 对话完成后的统一触发点(存当前轮 + 触发主模型覆写)
- SimpleMemoryService: fire-and-forget 主模型覆写编排 + RMW 锁
"""

from __future__ import annotations

from .core import SimpleMemoryCore
from .service import SimpleMemoryService

__all__ = ["SimpleMemoryCore", "SimpleMemoryService"]
