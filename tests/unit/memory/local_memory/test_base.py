"""Local Memory模块基础测试类

提供通用的测试工具和方法，确保测试的一致性和可维护性。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from tests.factories.memory.local_memory import (
    ConversationDataFactory,
    ConversationIndexFactory,
)
from tests.mocks.memory.local_memory import (
    ConversationMemoryCoreMocks,
    SimplePinnedMemoryManagerMocks,
    SplittableMemoryCacheMocks,
    create_mock_conversation_data,
)

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

    from src.storage.models.conversation import ConversationData, ConversationIndex


class BaseLocalMemoryTest:
    """Local Memory模块基础测试类"""

    @pytest.fixture
    def conversation_data_factory(self):
        """ConversationData工厂fixture"""
        return ConversationDataFactory()

    @pytest.fixture
    def conversation_index_factory(self):
        """ConversationIndex工厂fixture"""
        return ConversationIndexFactory()

    @pytest.fixture
    def sample_user_data(self, test_user, test_thread_id):
        """示例用户数据fixture"""
        return {
            "user_id": test_user,
            "thread_id": test_thread_id,
            "api_key": "sk-test-key-001",
        }

    @pytest.fixture
    def sample_conversation_data(self, sample_user_data):
        """示例对话数据fixture"""
        return create_mock_conversation_data(
            user_id=sample_user_data["user_id"], thread_id=sample_user_data["thread_id"]
        )

    @pytest.fixture
    def sample_conversation_batch(self, sample_user_data):
        """示例批量对话数据fixture"""
        factory = ConversationIndexFactory()
        return factory.create_batch(
            count=5,
            user_id=sample_user_data["user_id"],
            thread_id=sample_user_data["thread_id"],
        )

    def assert_conversation_data_equal(
        self, actual: ConversationData, expected: ConversationData
    ) -> None:
        """断言两个ConversationData对象相等（移除冗余的conversation_id检查）"""
        assert actual.user_id == expected.user_id
        assert actual.thread_id == expected.thread_id
        assert actual.user_message == expected.user_message
        assert actual.assistant_response == expected.assistant_response
        assert actual.round_number == expected.round_number

    def assert_memory_parts_contain(
        self, memory_parts: dict[str, Any], expected_content: str
    ) -> None:
        """断言记忆部分包含特定内容"""
        combined_content = ""

        if "pinned_memory" in memory_parts:
            pinned = memory_parts["pinned_memory"]
            if isinstance(pinned, dict):
                combined_content += str(pinned.get("formatted_content", ""))
                combined_content += (
                    pinned.get("basic_info", "")
                    + pinned.get("preferences", "")
                )
            else:
                combined_content += str(pinned)

        for part_name in ["index_area", "conversation_history"]:
            if part_name in memory_parts:
                part = memory_parts[part_name]
                if isinstance(part, dict):
                    combined_content += part.get("content", "")
                else:
                    combined_content += str(part)

        assert expected_content in combined_content, (
            f"Expected content '{expected_content}' not found in memory parts"
        )

    def assert_async_calls_made(
        self, mock_obj: AsyncMock, expected_calls: list[str]
    ) -> None:
        """断言异步方法被调用"""
        for call_name in expected_calls:
            assert getattr(mock_obj, call_name).called, (
                f"Expected method '{call_name}' was not called"
            )

    def assert_cache_operations(
        self, cache_mock: AsyncMock, hit_expected: bool, miss_expected: bool
    ) -> None:
        """断言缓存操作"""
        if hit_expected:
            cache_mock.get.assert_called()
            cache_mock.set.assert_not_called()
        else:
            cache_mock.get.assert_called()
            if miss_expected:
                cache_mock.set.assert_called()

    def create_user_isolation_test_data(
        self, user_count: int = 3
    ) -> dict[str, list[ConversationIndex]]:
        """创建用户隔离测试数据"""
        factory = ConversationIndexFactory()
        users_data = {}

        for i in range(user_count):
            user_id = f"test_user_{i + 1}"
            thread_id = f"{user_id}_main"

            conversations = factory.create_batch(
                count=5, user_id=user_id, thread_id=thread_id
            )
            users_data[user_id] = conversations

        return users_data

    def create_thread_isolation_test_data(
        self, user_id: str = "test_user", thread_count: int = 3
    ) -> dict[str, list[ConversationIndex]]:
        """创建线程隔离测试数据"""
        factory = ConversationIndexFactory()
        threads_data = {}

        for i in range(thread_count):
            thread_id = f"{user_id}_thread_{i + 1}"

            conversations = factory.create_batch(
                count=5, user_id=user_id, thread_id=thread_id
            )
            threads_data[thread_id] = conversations

        return threads_data

    async def assert_parallel_operations_consistency(
        self, conversation_data: ConversationData, operations_results: list[Any]
    ) -> None:
        """断言并行操作的一致性（移除冗余的conversation_id检查，使用round_number确保唯一性）"""
        # 确保所有操作使用相同的ConversationData
        for result in operations_results:
            if isinstance(result, dict) and "round_number" in result:
                assert result["round_number"] == conversation_data.round_number, (
                    "Parallel operations used different conversation data"
                )

    def create_error_scenarios(self) -> dict[str, Exception]:
        """创建错误场景"""
        return {
            "database_error": Exception("database connection failed"),
            "cache_error": Exception("缓存服务不可用"),
            "storage_error": Exception("存储空间不足"),
            "network_error": Exception("网络连接超时"),
            "validation_error": ValueError("数据验证失败"),
            "permission_error": PermissionError("权限不足"),
        }

    def setup_mocks_for_success_scenario(self, *mock_objects) -> None:
        """为成功场景设置Mock"""
        for mock_obj in mock_objects:
            if hasattr(mock_obj, "return_value"):
                mock_obj.return_value = None
            elif hasattr(mock_obj, "side_effect"):
                mock_obj.side_effect = None

    def setup_mocks_for_error_scenario(self, error_type: str, *mock_objects) -> None:
        """为错误场景设置Mock"""
        error_scenarios = self.create_error_scenarios()
        error = error_scenarios.get(error_type, Exception("未知错误"))

        for mock_obj in mock_objects:
            if hasattr(mock_obj, "side_effect"):
                mock_obj.side_effect = error

    def assert_error_handling(
        self, caught_exception: Exception, expected_error_type: type
    ) -> None:
        """断言错误处理"""
        assert isinstance(caught_exception, expected_error_type), (
            f"Expected {expected_error_type.__name__}, got {type(caught_exception).__name__}"
        )

    async def assert_concurrent_access_safety(
        self, concurrent_func, access_count: int = 10
    ) -> None:
        """断言并发访问安全性"""
        tasks = [concurrent_func() for _ in range(access_count)]

        # 等待所有任务完成，不应抛出异常
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 检查是否有异常
            exceptions = [r for r in results if isinstance(r, Exception)]
            assert len(exceptions) == 0, (
                f"Concurrent access caused exceptions: {exceptions}"
            )

        except Exception as e:
            pytest.fail(f"Concurrent access test failed with exception: {e}")

    def create_time_based_test_data(
        self, days_range: int = 30
    ) -> list[ConversationIndex]:
        """创建基于时间的测试数据"""
        factory = ConversationIndexFactory()
        return factory.create_with_time_range(days=days_range, conversations_per_day=2)

    def assert_time_ordering(
        self, conversations: list[ConversationIndex], reverse: bool = True
    ) -> None:
        """断言时间排序"""
        if not conversations:
            return

        sorted_conversations = sorted(
            conversations, key=lambda x: x.created_at, reverse=reverse
        )
        assert conversations == sorted_conversations, (
            "Conversations are not properly ordered by time"
        )


class MockMixin:
    """Mock功能混入类"""

    def setup_conversation_memory_core_mocks(self) -> ConversationMemoryCoreMocks:
        """设置ConversationMemoryCore Mock"""
        return ConversationMemoryCoreMocks()

    def setup_splittable_memory_cache_mocks(self) -> SplittableMemoryCacheMocks:
        """设置SplittableMemoryCache Mock"""
        return SplittableMemoryCacheMocks()

    def setup_simple_pinned_memory_manager_mocks(
        self,
    ) -> SimplePinnedMemoryManagerMocks:
        """设置SimplePinnedMemoryManager Mock"""
        return SimplePinnedMemoryManagerMocks()


class AsyncTestMixin:
    """异步测试功能混入类"""

    async def wait_for_with_timeout(self, coro, timeout: float = 5.0):
        """带超时的等待异步操作"""
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except TimeoutError:
            pytest.fail(f"Async operation timed out after {timeout} seconds")

    def assert_coroutine_function(self, func) -> None:
        """断言函数是协程函数"""
        assert asyncio.iscoroutinefunction(func), (
            f"Function {func.__name__} is not a coroutine function"
        )

    async def assert_no_awaitable_leak(self, coro) -> None:
        """断言没有可等待对象泄漏"""
        result = await coro
        # 确保结果不是协程或可等待对象
        assert not asyncio.iscoroutine(result), (
            "Function returned a coroutine instead of result"
        )
        assert not hasattr(result, "__await__"), "Function returned an awaitable object"


class AssertionMixin:
    """断言功能混入类"""

    def assert_memory_content_structure(self, memory_content: str) -> None:
        """断言记忆内容结构"""
        assert isinstance(memory_content, str), "Memory content should be a string"
        assert len(memory_content) > 0, "Memory content should not be empty"

    def assert_conversation_format(self, conversation_content: str) -> None:
        """断言对话格式"""
        assert "User:" in conversation_content or "User:" in conversation_content, (
            "Conversation should contain user messages"
        )
        assert (
            "Assistant:" in conversation_content or "Assistant:" in conversation_content
        ), "Conversation should contain assistant messages"

    def assert_todo_format(self, todo_content: str) -> None:
        """断言TODO格式"""
        assert "待办事项" in todo_content or "TODO" in todo_content, (
            "Todo content should contain todo indicator"
        )

    def assert_pinned_memory_format(self, pinned_content: str) -> None:
        """断言置顶记忆格式"""
        # 检查是否包含基础信息字段
        has_basic_info = any(
            keyword in pinned_content
            for keyword in ["基础信息", "姓名", "邮箱", "电话"]
        )
        has_preferences = any(
            keyword in pinned_content for keyword in ["偏好", "喜好", "习惯"]
        )

        # 至少应该包含一种信息
        assert has_basic_info or has_preferences, (
            "Pinned memory should contain basic info or preferences"
        )
