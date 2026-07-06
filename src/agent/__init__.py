"""Personal Agent Assistant - 无状态Agent架构的个人助手系统

基于工厂模式 + 无状态Agent的个人助手系统:
- AgentFactory: 统一的Agent工厂接口
- 无状态Agent: 完全无状态, 支持多用户并发
- 模块化处理器系统: BaseProcessor, LocalMemoryProcessor (messages 数组), InferenceCoordinator
- 处理器总协调器: ProcessorOrchestrator
"""

from __future__ import annotations

from .base_agent import BaseAgent
from .factory import AgentFactory
from .processors import (
    BaseProcessor,
    InferenceCoordinator,
    LocalMemoryProcessor,
    ProcessorOrchestrator,
)

__all__ = [
    "AgentFactory",
    "BaseAgent",
    "BaseProcessor",
    "InferenceCoordinator",
    "LocalMemoryProcessor",
    "ProcessorOrchestrator",
]
