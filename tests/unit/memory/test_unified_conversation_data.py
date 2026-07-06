"""统一对话数据源架构测试.

测试新的ConversationData统一数据源架构，确保四个并行操作使用相同的数据.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.memory.local_memory import pinned_memory_service
from src.agent.memory.local_memory.core import ConversationMemoryCore
from src.storage.models.conversation import (
    ConversationData,
)


class TestConversationMemoryCoreUnifiedData:
    """测试ConversationMemoryCore使用统一数据源."""

    @pytest.fixture
    def memory_core(self) -> ConversationMemoryCore:
        """创建ConversationMemoryCore实例."""
        from unittest.mock import MagicMock

        config = MagicMock()
        config.agent_id = "personal-assistant"
        return ConversationMemoryCore("test_user", "test_thread", agent_config=config)

    @pytest.fixture
    def sample_conversation_data(self) -> ConversationData:
        """创建示例ConversationData."""
        return ConversationData(
            user_id="test_user",
            thread_id="test_thread",
            agent_id="personal-assistant",
            user_message="测试用户消息",
            assistant_response="测试助手回复",
            round_number=1,
            timestamp=datetime.now(UTC),
        )

    @pytest.mark.asyncio
    async def test_add_conversation_round_with_unified_data(
        self,
        memory_core: ConversationMemoryCore,
        sample_conversation_data: ConversationData,
    ) -> None:
        """测试使用统一ConversationData添加对话轮次."""
        # 模拟四个并行操作
        with (
            patch.object(
                memory_core, "_store_conversation_content", new_callable=AsyncMock
            ) as mock_sql,
            patch.object(
                memory_core, "_store_vector_conversation", new_callable=AsyncMock
            ) as mock_vector,
            patch.object(
                memory_core._pinned_svc, "update", new_callable=AsyncMock
            ) as mock_pinned,
            patch.object(
                memory_core, "_generate_conversation_index", new_callable=AsyncMock
            ) as mock_index,
        ):
            # 调用添加对话轮次
            await memory_core.add_conversation_round(sample_conversation_data)

            # 验证所有四个方法都被调用，且使用相同的ConversationData
            mock_sql.assert_called_once_with(sample_conversation_data)
            mock_vector.assert_called_once_with(sample_conversation_data)
            mock_pinned.assert_called_once_with(sample_conversation_data)
            mock_index.assert_called_once_with(sample_conversation_data)

            # 验证 ConversationData 内容正确传递
            assert sample_conversation_data.user_message == "测试用户消息"
            assert sample_conversation_data.assistant_response == "测试助手回复"

    @pytest.mark.asyncio
    async def test_conversation_content_storage_method(
        self,
        memory_core: ConversationMemoryCore,
        sample_conversation_data: ConversationData,
    ) -> None:
        """测试对话内容存储方法使用ConversationData."""
        with patch(
            "src.agent.memory.local_memory.core.create_conversation_data_service"
        ) as mock_create:
            mock_manager = AsyncMock()
            mock_create.return_value = mock_manager

            await memory_core._store_conversation_content(sample_conversation_data)

            # 验证数据管理器被正确调用
            mock_create.assert_called_once_with(
                "test_user", "test_thread", agent_id="personal-assistant"
            )
            mock_manager.store_conversation_data.assert_called_once()

            # 获取调用参数
            call_args = mock_manager.store_conversation_data.call_args[0][
                0
            ]  # 第一个位置参数是ConversationData
            assert call_args.round_number == 1
            assert call_args.user_message == "测试用户消息"
            assert call_args.assistant_response == "测试助手回复"

    @pytest.mark.asyncio
    async def test_index_generation_method(
        self,
        memory_core: ConversationMemoryCore,
        sample_conversation_data: ConversationData,
    ) -> None:
        """测试索引生成方法使用ConversationData."""
        with (
            patch(
                "src.inference.content_analyzer.simple_analyzer.get_content_analyzer"
            ) as mock_get_analyzer,
            patch(
                "src.agent.memory.local_memory.core.create_conversation_service"
            ) as mock_create,
        ):
            # 模拟内容分析器
            mock_analyzer = AsyncMock()
            mock_get_analyzer.return_value = mock_analyzer
            mock_analyzer.model_id = "test-model"

            # 模拟分析结果
            mock_result = MagicMock()
            mock_result.topic = "测试主题"
            mock_result.summary = "测试摘要"
            mock_analyzer.analyze_conversation_index.return_value = mock_result

            # 模拟数据管理器
            mock_manager = AsyncMock()
            mock_create.return_value = mock_manager

            await memory_core._generate_conversation_index(sample_conversation_data)

            # 验证内容分析器被调用
            mock_analyzer.analyze_conversation_index.assert_called_once_with(
                "测试用户消息", "测试助手回复"
            )

            # 验证数据管理器被调用
            mock_manager.create_conversation.assert_called_once()

            # 获取调用参数
            call_args = mock_manager.create_conversation.call_args[1]
            assert call_args["user_message"] == "测试用户消息"
            assert call_args["assistant_response"] == "测试助手回复"

    @pytest.mark.asyncio
    async def test_index_generation_error_handling(
        self,
        memory_core: ConversationMemoryCore,
        sample_conversation_data: ConversationData,
    ) -> None:
        """测试索引生成方法的错误处理（生产模式下主模型失败的容错处理）."""
        with (
            patch(
                "src.inference.content_analyzer.simple_analyzer.get_content_analyzer"
            ) as mock_get_analyzer,
            patch.dict("os.environ", {"DEBUG": "false"}),
        ):
            mock_analyzer = AsyncMock()
            mock_get_analyzer.return_value = mock_analyzer
            mock_analyzer.analyze_conversation_index.side_effect = Exception(
                "分析器错误"
            )

            # 生产模式下不抛出异常
            await memory_core._generate_conversation_index(sample_conversation_data)

            mock_analyzer.analyze_conversation_index.assert_called_once()

    @pytest.mark.asyncio
    async def test_parallel_operations_data_consistency(
        self,
        memory_core: ConversationMemoryCore,
        sample_conversation_data: ConversationData,
    ) -> None:
        """测试四个并行操作使用相同的数据确保一致性."""
        # 用于存储各个操作接收到的数据
        received_data = {
            "sql": None,
            "vector": None,
            "pinned": None,
            "index": None,
        }

        async def capture_sql(data: ConversationData) -> None:
            received_data["sql"] = data

        async def capture_vector(data: ConversationData) -> None:
            received_data["vector"] = data

        async def capture_pinned(data: ConversationData) -> None:
            received_data["pinned"] = data

        async def capture_index(data: ConversationData) -> None:
            received_data["index"] = data

        # 替换方法
        memory_core._store_conversation_content = capture_sql  # type: ignore
        memory_core._store_vector_conversation = capture_vector  # type: ignore
        memory_core._pinned_svc.update = capture_pinned  # type: ignore
        memory_core._generate_conversation_index = capture_index  # type: ignore

        # 执行添加对话轮次
        await memory_core.add_conversation_round(sample_conversation_data)
        # 置顶更新已转 fire-and-forget, 等待后台任务完成再断言
        await asyncio.gather(
            *pinned_memory_service.get_bg_tasks(), return_exceptions=True
        )

        # 验证所有操作接收到相同的对象
        assert received_data["sql"] is sample_conversation_data
        assert received_data["vector"] is sample_conversation_data
        assert received_data["pinned"] is sample_conversation_data
        assert received_data["index"] is sample_conversation_data

        # 验证数据一致性（移除冗余的conversation_id检查，使用round_number确保唯一性）
        assert received_data["sql"].round_number == received_data["vector"].round_number
        assert received_data["sql"].user_message == received_data["pinned"].user_message
        assert (
            received_data["sql"].assistant_response
            == received_data["index"].assistant_response
        )
