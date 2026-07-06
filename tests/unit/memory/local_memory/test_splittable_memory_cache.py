"""SplittableMemoryCache 核心功能测试

测试3部分独立缓存系统的LRU淘汰机制、用户线程隔离、便捷函数等功能。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agent.memory.local_memory.cache import (
    SplittableMemoryCache,
    get_conversation,
    get_pinned_memory,
    get_splittable_memory_cache,
    get_todolist,
    set_conversation,
    set_pinned_memory,
    set_todolist,
)
from tests.unit.memory.local_memory.test_base import (
    AssertionMixin,
    AsyncTestMixin,
    BaseLocalMemoryTest,
    MockMixin,
)


class TestSplittableMemoryCache(
    BaseLocalMemoryTest, MockMixin, AsyncTestMixin, AssertionMixin
):
    """SplittableMemoryCache 核心功能测试类"""

    @pytest.fixture
    def memory_cache(self):
        """SplittableMemoryCache实例fixture"""
        return SplittableMemoryCache(
            max_pinned_memory_size=3, max_conversation_size=5, max_todolist_size=3
        )

    @pytest.fixture
    def sample_pinned_memory(self):
        """示例置顶记忆数据"""
        return "[Basic Info]\n姓名张三，程序员，30岁\n\n[Preferences]\n喜欢Python编程"

    @pytest.fixture
    def sample_conversation_data(self):
        """示例对话数据 (ConversationIndex 列表)"""
        return [
            {"round": 1, "user": "你好", "assistant": "您好！"},
            {"round": 2, "user": "谢谢", "assistant": "不客气！"},
        ]

    @pytest.fixture
    def sample_todo_list(self):
        """示例TODO列表数据"""
        return "- 🔴 完成项目文档\n- 🟡 代码审查"

    # ==================== 置顶记忆缓存测试 ====================

    def test_set_and_get_pinned_memory(
        self, memory_cache, sample_pinned_memory, test_user: str, test_thread_id: str
    ):
        """测试置顶记忆的设置和获取"""
        # 设置缓存
        memory_cache.set_pinned_memory(test_user, test_thread_id, sample_pinned_memory)

        # 获取缓存
        result = memory_cache.get_pinned_memory(test_user, test_thread_id)

        assert result == sample_pinned_memory

    def test_get_pinned_memory_not_exists(
        self, memory_cache, test_user: str, test_thread_id: str
    ):
        """测试获取不存在的置顶记忆"""
        result = memory_cache.get_pinned_memory(test_user, test_thread_id)

        assert result is None

    # ==================== 对话内容缓存测试 ====================

    def test_set_and_get_conversation(
        self,
        memory_cache,
        sample_conversation_data,
        test_user: str,
        test_thread_id: str,
    ):
        """测试对话内容的设置和获取"""
        # 设置缓存
        memory_cache.set_conversation(
            test_user, test_thread_id, sample_conversation_data
        )

        # 获取缓存
        result = memory_cache.get_conversation(test_user, test_thread_id)

        assert result == sample_conversation_data

    def test_get_conversation_not_exists(
        self, memory_cache, test_user: str, test_thread_id: str
    ):
        """测试获取不存在的对话内容"""
        result = memory_cache.get_conversation(test_user, test_thread_id)

        assert result is None

    def test_set_and_get_conversation_different_content_types(
        self, memory_cache, test_user: str, test_thread_id: str
    ):
        """测试不同内容类型的对话缓存"""
        user_id = test_user
        thread_id = test_thread_id

        # 空列表
        empty_list: list = []

        # 字典列表
        dict_list = [{"conversation": "对话内容", "metadata": {"length": 10}}]

        # 字符串列表
        str_list = ["对话轮次1", "对话轮次2"]

        # 测试不同类型
        memory_cache.set_conversation(user_id, thread_id, empty_list)
        result = memory_cache.get_conversation(user_id, thread_id)
        assert result == empty_list

        memory_cache.set_conversation(user_id, thread_id, dict_list)
        result = memory_cache.get_conversation(user_id, thread_id)
        assert result == dict_list

        memory_cache.set_conversation(user_id, thread_id, str_list)
        result = memory_cache.get_conversation(user_id, thread_id)
        assert result == str_list

    # ==================== TODO列表缓存测试 ====================

    def test_set_and_get_todolist(
        self, memory_cache, sample_todo_list, test_user: str, test_thread_id: str
    ):
        """测试TODO列表的设置和获取"""
        # 设置缓存
        memory_cache.set_todolist(test_user, test_thread_id, sample_todo_list)

        # 获取缓存
        result = memory_cache.get_todolist(test_user, test_thread_id)

        assert result == sample_todo_list

    def test_get_todolist_not_exists(
        self, memory_cache, test_user: str, test_thread_id: str
    ):
        """测试获取不存在的TODO列表"""
        result = memory_cache.get_todolist(test_user, test_thread_id)

        assert result is None

    def test_set_and_get_todolist_empty_list(
        self, memory_cache, test_user: str, test_thread_id: str
    ):
        """测试空TODO列表的缓存"""
        empty_todo = ""

        # 设置空列表
        memory_cache.set_todolist(test_user, test_thread_id, empty_todo)

        # 获取空列表
        result = memory_cache.get_todolist(test_user, test_thread_id)

        assert result == empty_todo

    # ==================== LRU淘汰机制测试 ====================

    def test_pinned_memory_lru_eviction(self, memory_cache, sample_pinned_memory):
        """测试置顶记忆的LRU淘汰机制"""
        # 缓存大小为3，添加4个不同的置顶记忆
        users = [
            ("user1", "thread1"),
            ("user2", "thread2"),
            ("user3", "thread3"),
            ("user4", "thread4"),
        ]

        # 添加3个置顶记忆（应该都在缓存中）
        for i, (user_id, thread_id) in enumerate(users[:3]):
            pinned_data = f"{user_id}的基础信息"
            memory_cache.set_pinned_memory(user_id, thread_id, pinned_data)

        # 验证前3个都在缓存中
        for i, (user_id, thread_id) in enumerate(users[:3]):
            result = memory_cache.get_pinned_memory(user_id, thread_id)
            assert result is not None
            assert result == f"{user_id}的基础信息"

        # 添加第4个置顶记忆，应该淘汰最早的（user1:thread1）
        pinned_data4 = "user4的基础信息"
        memory_cache.set_pinned_memory("user4", "thread4", pinned_data4)

        # 验证user1被淘汰，user4在缓存中
        assert memory_cache.get_pinned_memory("user1", "thread1") is None
        assert memory_cache.get_pinned_memory("user4", "thread4") is not None
        assert memory_cache.get_pinned_memory("user4", "thread4") == "user4的基础信息"

        # 验证user2和user3仍在缓存中
        assert memory_cache.get_pinned_memory("user2", "thread2") is not None
        assert memory_cache.get_pinned_memory("user3", "thread3") is not None

    def test_conversation_lru_eviction(self, memory_cache, sample_conversation_data):
        """测试对话内容的LRU淘汰机制"""
        # 缓存大小为5，添加6个不同的对话内容
        users = [
            ("user1", "thread1"),
            ("user2", "thread2"),
            ("user3", "thread3"),
            ("user4", "thread4"),
            ("user5", "thread5"),
            ("user6", "thread6"),
        ]

        # 添加5个对话内容（应该都在缓存中）
        for i, (user_id, thread_id) in enumerate(users[:5]):
            conv_data = [{"user": user_id, "round": 1}]
            memory_cache.set_conversation(user_id, thread_id, conv_data)

        # 验证前5个都在缓存中
        for i, (user_id, thread_id) in enumerate(users[:5]):
            result = memory_cache.get_conversation(user_id, thread_id)
            assert result is not None
            assert result[0]["user"] == user_id

        # 添加第6个对话内容，应该淘汰最早的（user1:thread1）
        conv_data6 = [{"user": "user6", "round": 1}]
        memory_cache.set_conversation("user6", "thread6", conv_data6)

        # 验证user1被淘汰，user6在缓存中
        assert memory_cache.get_conversation("user1", "thread1") is None
        assert memory_cache.get_conversation("user6", "thread6") is not None
        assert memory_cache.get_conversation("user6", "thread6")[0]["user"] == "user6"

    def test_todolist_lru_eviction(self, memory_cache, sample_todo_list):
        """测试TODO列表的LRU淘汰机制"""
        # 缓存大小为3，添加4个不同的TODO列表
        users = [
            ("user1", "thread1"),
            ("user2", "thread2"),
            ("user3", "thread3"),
            ("user4", "thread4"),
        ]

        # 添加3个TODO列表（应该都在缓存中）
        for i, (user_id, thread_id) in enumerate(users[:3]):
            todo_data = f"{user_id} todo"
            memory_cache.set_todolist(user_id, thread_id, todo_data)

        # 验证前3个都在缓存中
        for i, (user_id, thread_id) in enumerate(users[:3]):
            result = memory_cache.get_todolist(user_id, thread_id)
            assert result is not None

        # 添加第4个TODO列表，应该淘汰最早的（user1:thread1）
        todo_data4 = "user4 todo"
        memory_cache.set_todolist("user4", "thread4", todo_data4)

        # 验证user1被淘汰，user4在缓存中
        assert memory_cache.get_todolist("user1", "thread1") is None
        assert memory_cache.get_todolist("user4", "thread4") is not None

    # ==================== 全局缓存实例测试 ====================

    def test_get_splittable_memory_cache_singleton(self):
        """测试全局缓存实例单例模式"""
        with patch("src.agent.memory.local_memory.cache._global_cache", None):
            # 第一次调用应该创建新实例
            cache1 = get_splittable_memory_cache()

            # 第二次调用应该返回相同实例
            cache2 = get_splittable_memory_cache()

            assert cache1 is cache2
            assert isinstance(cache1, SplittableMemoryCache)

    def test_get_splittable_memory_cache_existing_instance(self):
        """测试获取已存在的全局缓存实例"""
        # 设置全局实例
        existing_cache = SplittableMemoryCache(max_pinned_memory_size=10)

        with patch("src.agent.memory.local_memory.cache._global_cache", existing_cache):
            # 获取应该返回已存在的实例
            cache = get_splittable_memory_cache()

            assert cache is existing_cache
            assert cache._pinned_memory_cache.maxsize == 10

    # ==================== 便捷函数测试 ====================

    def test_convenience_functions(
        self, sample_pinned_memory, sample_conversation_data, sample_todo_list
    ):
        """测试便捷函数"""
        user_id = "convenience_user"
        thread_id = "convenience_thread"

        # 便捷函数依赖全局缓存实例；为避免被其他用例的patch影响，这里显式注入真实实例
        with patch(
            "src.agent.memory.local_memory.cache._global_cache",
            SplittableMemoryCache(
                max_pinned_memory_size=10,
                max_conversation_size=10,
                max_todolist_size=10,
            ),
        ):
            # 测试置顶记忆便捷函数
            set_pinned_memory(user_id, thread_id, sample_pinned_memory)
            result = get_pinned_memory(user_id, thread_id)
            assert result == sample_pinned_memory

            # 测试对话内容便捷函数
            set_conversation(user_id, thread_id, sample_conversation_data)
            result = get_conversation(user_id, thread_id)
            assert result == sample_conversation_data

            # 测试TODO列表便捷函数
            set_todolist(user_id, thread_id, sample_todo_list)
            result = get_todolist(user_id, thread_id)
            assert result == sample_todo_list

    def test_convenience_functions_with_global_cache(self, sample_pinned_memory):
        """测试便捷函数使用全局缓存"""
        user_id = "global_user"
        thread_id = "global_thread"

        with patch(
            "src.agent.memory.local_memory.cache._global_cache",
            SplittableMemoryCache(max_pinned_memory_size=10),
        ):
            # 使用便捷函数设置缓存
            set_pinned_memory(user_id, thread_id, sample_pinned_memory)

            # 直接使用全局缓存获取
            global_cache = get_splittable_memory_cache()
            result = global_cache.get_pinned_memory(user_id, thread_id)

            assert result == sample_pinned_memory

    # ==================== 多组件集成测试 ====================

    def test_multi_component_independence(
        self,
        memory_cache,
        sample_pinned_memory,
        sample_conversation_data,
        sample_todo_list,
    ):
        """测试多组件的独立性"""
        user_id = "independent_user"
        thread_id = "independent_thread"

        # 设置不同类型的缓存
        memory_cache.set_pinned_memory(user_id, thread_id, sample_pinned_memory)
        memory_cache.set_conversation(user_id, thread_id, sample_conversation_data)
        memory_cache.set_todolist(user_id, thread_id, sample_todo_list)

        # 分别获取不同类型的缓存
        pinned_result = memory_cache.get_pinned_memory(user_id, thread_id)
        conversation_result = memory_cache.get_conversation(user_id, thread_id)
        todo_result = memory_cache.get_todolist(user_id, thread_id)

        # 验证独立性
        assert pinned_result == sample_pinned_memory
        assert conversation_result == sample_conversation_data
        assert todo_result == sample_todo_list

        # 清除一个组件不应该影响其他组件
        memory_cache.set_pinned_memory(user_id, thread_id, None)

        assert memory_cache.get_pinned_memory(user_id, thread_id) is None
        assert (
            memory_cache.get_conversation(user_id, thread_id)
            == sample_conversation_data
        )
        assert memory_cache.get_todolist(user_id, thread_id) == sample_todo_list

    def test_cache_isolation_across_components(self, memory_cache):
        """测试跨组件的缓存隔离"""
        user1_id = "user1"
        user2_id = "user2"
        thread_id = "common_thread"

        # 用户1设置所有组件的缓存
        memory_cache.set_pinned_memory(user1_id, thread_id, "user1 pinned")
        memory_cache.set_conversation(
            user1_id, thread_id, [{"user": "user1", "type": "conversation"}]
        )
        memory_cache.set_todolist(user1_id, thread_id, "user1 todo")

        # 用户2设置所有组件的缓存
        memory_cache.set_pinned_memory(user2_id, thread_id, "user2 pinned")
        memory_cache.set_conversation(
            user2_id, thread_id, [{"user": "user2", "type": "conversation"}]
        )
        memory_cache.set_todolist(user2_id, thread_id, "user2 todo")

        # 验证用户隔离
        pinned_result1 = memory_cache.get_pinned_memory(user1_id, thread_id)
        pinned_result2 = memory_cache.get_pinned_memory(user2_id, thread_id)
        assert pinned_result1 == "user1 pinned"
        assert pinned_result2 == "user2 pinned"

        conversation_result1 = memory_cache.get_conversation(user1_id, thread_id)
        conversation_result2 = memory_cache.get_conversation(user2_id, thread_id)
        assert conversation_result1[0]["user"] == "user1"
        assert conversation_result2[0]["user"] == "user2"

        todo_result1 = memory_cache.get_todolist(user1_id, thread_id)
        todo_result2 = memory_cache.get_todolist(user2_id, thread_id)
        assert todo_result1 == "user1 todo"
        assert todo_result2 == "user2 todo"

    # ==================== Agent隔离测试 ====================

    def test_agent_isolation_same_user_thread(self, memory_cache):
        """测试相同用户线程下不同Agent的缓存隔离.

        这是数据隔离的关键测试: 同一个user_id和thread_id下,
        不同agent的缓存应该完全隔离, 互不可见.
        """
        user_id = "alice"
        thread_id = "main"

        # personal-assistant设置缓存
        memory_cache.set_pinned_memory(
            user_id, thread_id, "personal数据", agent_id="personal-assistant"
        )
        memory_cache.set_conversation(
            user_id, thread_id, ["personal对话"], agent_id="personal-assistant"
        )
        memory_cache.set_todolist(
            user_id, thread_id, "personal todo", agent_id="personal-assistant"
        )

        # health-assistant设置缓存
        memory_cache.set_pinned_memory(
            user_id, thread_id, "health数据", agent_id="health-assistant"
        )
        memory_cache.set_conversation(
            user_id, thread_id, ["health对话"], agent_id="health-assistant"
        )
        memory_cache.set_todolist(
            user_id, thread_id, "health todo", agent_id="health-assistant"
        )

        # 验证personal-assistant读取自己的数据
        assert (
            memory_cache.get_pinned_memory(
                user_id, thread_id, agent_id="personal-assistant"
            )
            == "personal数据"
        )
        assert memory_cache.get_conversation(
            user_id, thread_id, agent_id="personal-assistant"
        ) == ["personal对话"]
        assert (
            memory_cache.get_todolist(user_id, thread_id, agent_id="personal-assistant")
            == "personal todo"
        )

        # 验证health-assistant读取自己的数据
        assert (
            memory_cache.get_pinned_memory(
                user_id, thread_id, agent_id="health-assistant"
            )
            == "health数据"
        )
        assert memory_cache.get_conversation(
            user_id, thread_id, agent_id="health-assistant"
        ) == ["health对话"]
        assert (
            memory_cache.get_todolist(user_id, thread_id, agent_id="health-assistant")
            == "health todo"
        )

    def test_no_agent_id_does_not_pollute_other_agent(self, memory_cache):
        """测试无agent_id的缓存不会影响有agent_id的缓存."""
        user_id = "alice"
        thread_id = "main"

        # 无agent_id设置缓存
        memory_cache.set_conversation(user_id, thread_id, ["无agent数据"])

        # 有agent_id设置缓存
        memory_cache.set_conversation(
            user_id, thread_id, ["health对话"], agent_id="health-assistant"
        )

        # 验证互不干扰
        assert memory_cache.get_conversation(user_id, thread_id) == ["无agent数据"]
        assert memory_cache.get_conversation(
            user_id, thread_id, agent_id="health-assistant"
        ) == ["health对话"]
