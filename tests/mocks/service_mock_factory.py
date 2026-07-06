"""存储层Service Mock工厂.

提供标准化的Service层Mock对象，确保测试的一致性和可维护性。
遵循"Mock Service而不是Mock DAO"的原则，专注于业务逻辑测试。

设计原则:
1. 接口完整 - Mock所有公共业务方法
2. 返回值合理 - 提供有意义的默认返回值
3. 易于扩展 - 支持方法覆盖和自定义
4. 类型安全 - 保持与真实Service一致的签名
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.storage.models.conversation import ConversationIndex
from src.storage.models.todo import TodoItem


class ServiceMockFactory:
    """存储层Service Mock工厂.

    专门创建存储Service相关的Mock对象，模拟Service层业务逻辑。
    遵循单元测试原则：Mock Service层，专注于业务逻辑测试。
    """

    # 默认配置常量
    DEFAULT_USER_ID = "test_user"
    DEFAULT_THREAD_ID = "test_thread"
    # DEFAULT_CONVERSATION_ID = "test_conv_001"  # 已废弃：使用 round_number 替代
    DEFAULT_ROUND_NUMBER = 1
    DEFAULT_TODO_ID = 1
    DEFAULT_DOC_ID = "doc_123"

    @classmethod
    def create_conversation_service(cls, **overrides) -> AsyncMock:
        """创建ConversationService Mock.

        Args:
            **overrides: 方法覆盖，用于定制特定行为

        Returns:
            配置好所有公共接口的AsyncMock
        """
        mock_service = AsyncMock()

        # 业务方法
        mock_service.create_conversation = AsyncMock(
            return_value=ConversationIndex(
                id=1,
                round_number=cls.DEFAULT_ROUND_NUMBER,
                user_id=cls.DEFAULT_USER_ID,
                thread_id=cls.DEFAULT_THREAD_ID,
                user_message="Mock user message",
                assistant_response="Mock assistant response",
            )
        )

        mock_service.allocate_round_number = AsyncMock(
            return_value=cls.DEFAULT_ROUND_NUMBER
        )

        mock_service.get_latest_round_number = AsyncMock(return_value=0)

        mock_service.get_conversation_by_round = AsyncMock(return_value=None)

        mock_service.get_formatted_index_range = AsyncMock(
            return_value="### 📚 历史对话索引\n\n| 轮次 | 主题 |\n|------|------|"
        )

        # 健康检查
        mock_service.health_check = AsyncMock(
            return_value={
                "status": "healthy",
                "message": "对话服务正常",
                "details": {"conversation_count": 0, "index_count": 0},
            }
        )

        # 统计信息
        mock_service.get_conversation_statistics = AsyncMock(
            return_value={
                "total_conversations": 0,
                "total_rounds": 0,
                "latest_round": 0,
            }
        )

        # 属性
        mock_service.session_factory = MagicMock()

        # 允许覆盖
        for key, value in overrides.items():
            setattr(mock_service, key, value)

        return mock_service

    @classmethod
    def create_todo_service(cls, **overrides) -> AsyncMock:
        """创建TodoService Mock.

        Args:
            **overrides: 方法覆盖，用于定制特定行为

        Returns:
            配置好所有公共接口的AsyncMock
        """
        mock_service = AsyncMock()

        # CRUD方法
        mock_service.list_todos = AsyncMock(return_value=[])

        mock_service.create_todo = AsyncMock(
            return_value=TodoItem(
                id=cls.DEFAULT_TODO_ID,
                title="测试任务",
                user_id=cls.DEFAULT_USER_ID,
                thread_id=cls.DEFAULT_THREAD_ID,
                status="pending",
                priority="medium",
            )
        )

        mock_service.update_todo = AsyncMock(return_value=True)

        mock_service.delete_todo = AsyncMock(return_value=True)

        mock_service.get_todo_by_id = AsyncMock(return_value=None)

        # 格式化方法
        mock_service.format_todos = AsyncMock(return_value="### 📋 任务列表\n暂无任务")

        mock_service.get_formatted_todolist = AsyncMock(
            return_value="### 📋 进行中的任务\n- 🟡 测试任务"
        )

        # 健康检查
        mock_service.health_check = AsyncMock(
            return_value={
                "status": "healthy",
                "message": "TODO服务正常",
                "details": {
                    "pending_count": 0,
                    "completed_count": 0,
                },
            }
        )

        # 统计信息
        mock_service.get_statistics = AsyncMock(
            return_value={
                "total": 0,
                "by_status": {"pending": 0, "completed": 0},
                "by_priority": {"high": 0, "normal": 0, "low": 0},
            }
        )

        # 属性
        mock_service.session_factory = MagicMock()

        # 允许覆盖
        for key, value in overrides.items():
            setattr(mock_service, key, value)

        return mock_service

    @classmethod
    def create_memory_service(cls, **overrides) -> AsyncMock:
        """创建MemoryService Mock.

        Args:
            **overrides: 方法覆盖，用于定制特定行为

        Returns:
            配置好所有公共接口的AsyncMock
        """
        mock_service = AsyncMock()

        # 记忆获取方法
        mock_service.get_pinned_memory_as_dict = AsyncMock(return_value={})

        mock_service.format_pinned_memory_dict = AsyncMock(
            return_value="### 👤 基本信息\n测试用户"
        )

        # 记忆更新方法
        mock_service.update_memory = AsyncMock(return_value=None)

        # 记忆查询方法
        mock_service.get_memory_by_type = AsyncMock(return_value=None)

        mock_service.get_all_memories = AsyncMock(return_value=[])

        mock_service.delete_memory = AsyncMock(return_value=True)

        mock_service.get_memory_types_status = AsyncMock(
            return_value={"action": 0, "character": 0, "knowledge": 0}
        )

        # 健康检查
        mock_service.health_check = AsyncMock(
            return_value={
                "status": "healthy",
                "message": "记忆服务正常",
                "details": {"memory_types": 3},
            }
        )

        # 属性
        mock_service.session_factory = MagicMock()

        # 允许覆盖
        for key, value in overrides.items():
            setattr(mock_service, key, value)

        return mock_service

    @classmethod
    def create_vector_service(cls, **overrides) -> AsyncMock:
        """创建VectorService Mock.

        Args:
            **overrides: 方法覆盖，用于定制特定行为

        Returns:
            配置好所有公共接口的AsyncMock
        """
        mock_service = AsyncMock()

        # 向量存储方法
        mock_service.add_conversation_content = AsyncMock(
            return_value=cls.DEFAULT_DOC_ID
        )

        mock_service.search_conversations = AsyncMock(return_value=[])

        mock_service.search_conversation_rounds = AsyncMock(return_value=[])

        mock_service.search_conversation_rounds_mmr = AsyncMock(return_value=[])

        # 统计方法
        mock_service.get_collection_stats = AsyncMock(
            return_value={
                "document_count": 0,
                "collection_name": f"{cls.DEFAULT_USER_ID}_{cls.DEFAULT_THREAD_ID}",
            }
        )

        # 健康检查
        mock_service.health_check = AsyncMock(
            return_value={
                "status": "healthy",
                "vector_store_initialized": True,
                "collection_stats": {"document_count": 0},
            }
        )

        # 属性
        mock_service.user_id = cls.DEFAULT_USER_ID
        mock_service.thread_id = cls.DEFAULT_THREAD_ID
        mock_service.vector_store = AsyncMock()

        # 允许覆盖
        for key, value in overrides.items():
            setattr(mock_service, key, value)

        return mock_service

    @classmethod
    def create_all_services(cls, **overrides) -> dict[str, AsyncMock]:
        """创建所有Service的Mock实例.

        Args:
            **overrides: 全局覆盖配置

        Returns:
            包含所有Service mock的字典:
            - conversation: ConversationService mock
            - todo: TodoService mock
            - memory: MemoryService mock
            - vector: VectorService mock
        """
        services = {
            "conversation": cls.create_conversation_service(),
            "todo": cls.create_todo_service(),
            "memory": cls.create_memory_service(),
            "vector": cls.create_vector_service(),
        }

        # 应用全局覆盖
        for service_name, service_mock in services.items():
            service_overrides = overrides.get(service_name, {})
            for key, value in service_overrides.items():
                setattr(service_mock, key, value)

        return services

    @classmethod
    def create_service_error_scenario(
        cls, error_type: str = "database", service_name: str = "conversation"
    ) -> AsyncMock:
        """创建Service错误场景Mock.

        Args:
            error_type: 错误类型 (database, timeout, validation)
            service_name: 服务名称 (conversation, todo, memory, vector)

        Returns:
            配置好错误行为的Service Mock

        Raises:
            ValueError: 当服务名称不支持时
        """
        if error_type == "database":
            error = Exception("数据库连接失败")
        elif error_type == "timeout":
            error = TimeoutError("操作超时")
        elif error_type == "validation":
            error = ValueError("输入验证失败")
        else:
            error = Exception(f"未知错误类型: {error_type}")

        # 创建对应Service的mock
        if service_name == "conversation":
            mock_service = cls.create_conversation_service()
            mock_service.create_conversation = AsyncMock(side_effect=error)
        elif service_name == "todo":
            mock_service = cls.create_todo_service()
            mock_service.create_todo = AsyncMock(side_effect=error)
        elif service_name == "memory":
            mock_service = cls.create_memory_service()
            mock_service.update_memory = AsyncMock(side_effect=error)
        elif service_name == "vector":
            mock_service = cls.create_vector_service()
            mock_service.add_conversation_content = AsyncMock(side_effect=error)
        else:
            raise ValueError(f"不支持的服务名称: {service_name}")

        # 健康检查应该返回unhealthy状态
        mock_service.health_check = AsyncMock(
            return_value={
                "status": "unhealthy",
                "message": f"{error_type}错误",
                "details": {"error": str(error)},
            }
        )

        return mock_service
