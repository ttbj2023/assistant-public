"""内容分析器模块 - 通用结构化分析服务.

这个模块提供统一的内容分析功能,支持多种分析类型:
- conversation_index - 对话索引生成
- pinned_memory_update - 置顶记忆更新判断

设计原则:
- 通用性:可被多个模块复用,不仅限于记忆系统
- 配置驱动:遵循项目统一配置系统
- 类型安全:使用Pydantic进行数据验证
- 异步支持:全面支持异步操作

使用示例:
```python
from src.inference.content_analyzer import SimpleContentAnalyzer

analyzer = SimpleContentAnalyzer()
result = await analyzer.analyze_conversation_index(user_msg, assistant_msg)
```
"""

from __future__ import annotations

from src.core.types import (
    ConversationIndexResult,
    MemoryOperation,
    PinnedMemoryUpdateResult,
)

from .simple_analyzer import SimpleContentAnalyzer, get_content_analyzer

__all__ = [
    "ConversationIndexResult",
    "MemoryOperation",
    "PinnedMemoryUpdateResult",
    "SimpleContentAnalyzer",
    "get_content_analyzer",
]
