"""Simple 模式记忆子系统.

轻量记忆架构: 仅管理当前轮记录.
对话历史由前端透传给 LLM, 不由本子系统组装.

核心组件:
- SimpleMemoryCore: 对话完成后的统一触发点(存当前轮)

注意: 长期记忆(经验洞察)已迁移至 domain_data 子系统, 不再由此模块管理.
"""

from __future__ import annotations

from .core import SimpleMemoryCore

__all__ = ["SimpleMemoryCore"]
