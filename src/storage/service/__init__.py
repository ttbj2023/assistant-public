"""Storage Service Layer.

提供存储层的业务逻辑封装,采用组合模式设计.

主要Service类:
- ConversationService: 对话业务逻辑
- TodoService: TODO业务逻辑
- MemoryService: 记忆业务逻辑
- VectorService: 向量存储业务逻辑
- RetrievalService: 检索服务接口
- DualStageRetrievalService: 双阶段检索服务实现
- HealthDataService: 健康数据业务逻辑
- ScheduledMessageService: 定时消息业务逻辑
- UserChannelConfigService: 用户渠道配置业务逻辑

注意: 附件管理已迁至 src/files/ 子系统 (FileRepository + ImageDescriber),
不再属于 storage/service 层.

使用示例:
```python
from src.storage.service import (
    create_conversation_service,
    create_todo_service,
    create_memory_service,
    create_vector_service,
    create_retrieval_service,
    create_health_service,
    create_scheduled_message_service,
)

# 创建Service实例
conv_service = await create_conversation_service(user_id, thread_id, agent_id=agent_id)
todo_service = await create_todo_service(user_id, thread_id, agent_id=agent_id)
vector_service = create_vector_service(user_id, thread_id, agent_id=agent_id)
retrieval_service = await create_retrieval_service(user_id, thread_id, agent_id=agent_id)
health_service = await create_health_service(user_id, thread_id, agent_id=agent_id)
msg_service = await create_scheduled_message_service(user_id, thread_id, agent_id=agent_id)
```
"""

from __future__ import annotations

from .conversation_service import ConversationService
from .health_check_mixin import ServiceHealthCheckMixin
from .memory_service import MemoryService
from .pinned_memory_block_service import PinnedMemoryBlockService
from .retrieval_service import DualStageRetrievalService, RetrievalService
from .scheduled_message_service import ScheduledMessageService
from .service_factory import (
    create_conversation_service,
    create_health_service,
    create_memory_service,
    create_pinned_memory_block_service,
    create_retrieval_service,
    create_scheduled_message_service,
    create_todo_service,
    create_usage_service,
    create_vector_service,
)
from .storage_health_aggregator import StorageHealthAggregator
from .todo_service import TodoService
from .usage_service import UsageService
from .user_channel_config_service import UserChannelConfigService
from .vector_service import VectorService

__all__ = [
    "ConversationService",
    "DualStageRetrievalService",
    "MemoryService",
    "PinnedMemoryBlockService",
    "RetrievalService",
    "ScheduledMessageService",
    "ServiceHealthCheckMixin",
    "StorageHealthAggregator",
    "TodoService",
    "UsageService",
    "UserChannelConfigService",
    "VectorService",
    "create_conversation_service",
    "create_health_service",
    "create_memory_service",
    "create_pinned_memory_block_service",
    "create_retrieval_service",
    "create_scheduled_message_service",
    "create_todo_service",
    "create_usage_service",
    "create_vector_service",
]
