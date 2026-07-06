"""核心模块 - 基础设施和公共组件

包含:
- cache: 高性能缓存系统
- validation: 输入验证和安全防护
- path_resolver: 用户隔离的文件系统路径管理
- client_manager: HTTP客户端和模型管理
- types: 通用数据类型
- streaming: 流式响应类型定义和工具函数
"""

from __future__ import annotations

# 流式响应类型定义
from .streaming import (
    StreamChunk,
    StreamContent,
    create_stream_chunk,
    create_stream_error_chunk,
    create_stream_final_chunk,
    format_sse_chunk,
    generate_completion_id,
)

# 通用数据类型
from .types import (
    ConversationIndexResult,
    MemoryOperation,
    PinnedMemoryUpdateResult,
)

__all__ = [
    # 数据类型
    "ConversationIndexResult",
    "MemoryOperation",
    "PinnedMemoryUpdateResult",
    # 流式响应
    "StreamChunk",
    "StreamContent",
    "create_stream_chunk",
    "create_stream_error_chunk",
    "create_stream_final_chunk",
    "format_sse_chunk",
    "generate_completion_id",
]
