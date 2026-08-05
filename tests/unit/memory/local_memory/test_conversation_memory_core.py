"""ConversationMemoryCore 核心功能测试

测试对话记忆核心的并行操作、数据一致性、异常处理等关键功能。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.memory.local_memory.core import ConversationMemoryCore
from src.config.agent_config import AgentConfig
from tests.mocks.memory.local_memory import (
    create_mock_conversation_data,
)
from tests.unit.memory.local_memory.test_base import (
    AssertionMixin,
    AsyncTestMixin,
    BaseLocalMemoryTest,
    MockMixin,
)


class TestConversationMemoryCore(
    BaseLocalMemoryTest, MockMixin, AsyncTestMixin, AssertionMixin
):
    """ConversationMemoryCore 核心功能测试类"""

    @pytest.fixture
    def conversation_memory_core(self, sample_user_data):
        """ConversationMemoryCore实例fixture"""
        config = AgentConfig()
        return ConversationMemoryCore(
            user_id=sample_user_data["user_id"],
            thread_id=sample_user_data["thread_id"],
            agent_config=config,
        )

    @pytest.fixture
    def conversation_memory_core_with_config(self, sample_user_data):
        """带配置的ConversationMemoryCore实例fixture"""
        config = AgentConfig()
        return ConversationMemoryCore(
            user_id=sample_user_data["user_id"],
            thread_id=sample_user_data["thread_id"],
            agent_config=config,
        )

    # ==================== 基础功能测试 ====================

    @pytest.mark.asyncio
    async def test_update_conversation_cache_rolling_trim(
        self, conversation_memory_core
    ):
        """写路径滚动裁剪: 窗口总量恒 <= budget, 超限丢最老轮次.

        回归 fc3e7f78 引入的全量 append 式缓存(无限膨胀) -> 滚动有界窗口.
        """
        from src.agent.memory.local_memory.cache import (
            get_conversation,
            reset_global_cache,
            set_conversation,
        )
        from src.storage.models.conversation import ConversationIndex

        reset_global_cache()
        try:
            uid = conversation_memory_core.user_id
            tid = conversation_memory_core.thread_id
            aid = conversation_memory_core.agent_id
            # 预算 1000: 每轮 300 字符(user/asst 各 150), 3 轮 900 <= 1000, 4 轮 1200 > 1000
            conversation_memory_core.agent_config.memory.total_char_budget = 1000

            half = "x" * 150
            seed = [
                ConversationIndex(
                    round_number=r, user_message=half, assistant_response=half
                )
                for r in (1, 2, 3)
            ]
            set_conversation(uid, tid, seed, agent_id=aid)

            data = create_mock_conversation_data(
                user_id=uid,
                thread_id=tid,
                agent_id=aid,
                round_number=4,
                user_message=half,
                assistant_response=half,
            )
            await conversation_memory_core._update_conversation_cache(data)

            result = get_conversation(uid, tid, agent_id=aid)
            assert isinstance(result, list)
            # 最老的轮 1 被裁掉, 窗口保持 3 轮(总量 900 <= 1000)
            assert [c.round_number for c in result] == [2, 3, 4]
            total = sum(len(c.user_message) + len(c.assistant_response) for c in result)
            assert total <= 1000
        finally:
            reset_global_cache()

    @pytest.mark.asyncio
    async def test_update_conversation_cache_skips_when_unseeded(
        self, conversation_memory_core
    ):
        """缓存未初始化(冷启动由读路径种子化)时写路径应跳过, 不凭空种入单轮."""
        from src.agent.memory.local_memory.cache import (
            get_conversation,
            reset_global_cache,
        )

        reset_global_cache()
        try:
            uid = conversation_memory_core.user_id
            tid = conversation_memory_core.thread_id

            data = create_mock_conversation_data(
                user_id=uid, thread_id=tid, round_number=1
            )
            await conversation_memory_core._update_conversation_cache(data)

            # 未种子化 -> 跳过, 缓存仍为 None(交由读路径冷启动)
            assert get_conversation(uid, tid, agent_id=data.agent_id) is None
        finally:
            reset_global_cache()

    # ==================== 6个并行操作测试 ====================

    @pytest.mark.asyncio
    async def test_add_conversation_round_database_error_handling(
        self, conversation_memory_core, sample_conversation_data
    ):
        """测试并行任务中数据库错误的容错处理"""
        from tests.mocks.service_mock_factory import ServiceMockFactory

        # 使用ServiceMockFactory创建错误场景
        error_service = ServiceMockFactory.create_service_error_scenario(
            error_type="database", service_name="conversation"
        )

        # 创建其他service的mock（避免真实初始化）
        mock_vector_service = AsyncMock()
        mock_analyzer = AsyncMock()
        mock_analyzer_instance2 = AsyncMock()
        mock_analyzer_instance2.analyze_conversation_index.return_value = MagicMock(
            topic="测试", summary="测试"
        )
        mock_analyzer.return_value = mock_analyzer_instance2

        # 需要完整mock所有service以避免真实数据库初始化
        with (
            patch(
                "src.agent.memory.local_memory.core.create_conversation_service",
                return_value=error_service,
            ),
            patch(
                "src.agent.memory.local_memory.core.create_vector_service",
                return_value=mock_vector_service,
            ),
            patch(
                "src.inference.content_analyzer.simple_analyzer.get_content_analyzer",
                return_value=mock_analyzer,
            ),
        ):
            # 执行测试 - 并行任务中的错误不会导致整个方法失败
            # 异常会被asyncio.gather捕获并作为结果返回
            await conversation_memory_core.add_conversation_round(
                sample_conversation_data
            )

            # add_conversation_round 正常完成(异常被 asyncio.gather 容错捕获)


class TestConversationMemoryCoreEmbeddingsConfig(
    BaseLocalMemoryTest, MockMixin, AsyncTestMixin, AssertionMixin
):
    """ConversationMemoryCore 嵌入模型配置测试类"""

    @pytest.fixture
    def conversation_memory_core(self, sample_user_data):
        """ConversationMemoryCore实例fixture"""
        config = AgentConfig()
        return ConversationMemoryCore(
            user_id=sample_user_data["user_id"],
            thread_id=sample_user_data["thread_id"],
            agent_config=config,
        )

    @pytest.mark.asyncio
    async def test_vector_storage_skipped_when_embeddings_disabled(
        self, conversation_memory_core, sample_conversation_data
    ):
        """测试embeddings.enabled=false时跳过向量存储"""
        from tests.mocks.service_mock_factory import ServiceMockFactory

        # 强制设置embeddings.enabled=false
        conversation_memory_core._embeddings_enabled = False

        # 创建Mock服务
        services = ServiceMockFactory.create_all_services()

        with (
            patch(
                "src.agent.memory.local_memory.core.create_vector_service"
            ) as mock_create_vec,
            patch(
                "src.inference.content_analyzer.simple_analyzer.get_content_analyzer"
            ),
        ):
            # 设置向量服务Mock
            mock_create_vec.return_value = services["vector"]

            # 执行添加对话轮次
            await conversation_memory_core.add_conversation_round(
                sample_conversation_data
            )

            # 验证向量存储服务未被调用
            assert services["vector"].add_conversation_content.call_count == 0

    @pytest.mark.asyncio
    async def test_vector_storage_executed_when_embeddings_enabled(
        self, conversation_memory_core, sample_conversation_data
    ):
        """测试embeddings.enabled=true时执行向量存储"""
        from tests.mocks.service_mock_factory import ServiceMockFactory

        # 确保embeddings.enabled=true（默认值）
        conversation_memory_core._embeddings_enabled = True

        # 创建Mock服务
        services = ServiceMockFactory.create_all_services()

        with (
            patch(
                "src.agent.memory.local_memory.core.create_vector_service"
            ) as mock_create_vec,
            patch(
                "src.inference.content_analyzer.simple_analyzer.get_content_analyzer"
            ),
        ):
            # 设置向量服务Mock
            mock_create_vec.return_value = services["vector"]

            # 执行添加对话轮次
            await conversation_memory_core.add_conversation_round(
                sample_conversation_data
            )

            # 验证向量存储服务被调用（ConversationMemoryCore._store_vector_conversation）
            assert services["vector"].add_conversation_content.call_count == 1
