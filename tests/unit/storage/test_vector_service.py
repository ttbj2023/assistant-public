"""VectorService 单元测试.

测试向量存储服务的功能，包括：
- 基本的CRUD操作
- 错误处理
- 生命周期管理
- 延迟初始化
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.storage.models.conversation import ConversationData
from src.storage.service.vector_service import VectorService


class TestVectorService:
    """VectorService 测试类."""

    @pytest.fixture
    def sample_conversation_data(self, test_user: str) -> ConversationData:
        """创建示例对话数据."""
        from datetime import datetime

        return ConversationData(
            user_id=test_user,
            thread_id="test_thread",
            agent_id="personal-assistant",
            user_message="测试用户消息",
            assistant_response="测试助手回复",
            round_number=1,
            timestamp=datetime.now(),
        )

    @pytest.fixture
    def mock_vector_store(self) -> AsyncMock:
        """创建mock向量存储."""
        mock_store = AsyncMock()
        mock_store.add_conversation_round = AsyncMock(return_value="doc_123")
        mock_store.similarity_search = AsyncMock(return_value=[])
        mock_store.search_rounds_only = AsyncMock(return_value=[])
        mock_store.search_rounds_mmr_only = AsyncMock(return_value=[])
        mock_store.get_collection_stats = MagicMock(return_value={"document_count": 0})
        mock_store.close = MagicMock()
        return mock_store

    @pytest.fixture
    def vector_service(
        self, mock_vector_store: AsyncMock, test_user: str
    ) -> VectorService:
        """创建VectorService实例."""
        return VectorService(test_user, "test_thread", mock_vector_store)

    @pytest.mark.asyncio
    async def test_add_conversation_content_failure(
        self, sample_conversation_data: ConversationData, test_user: str
    ) -> None:
        """测试添加对话内容失败."""
        # Arrange - 模拟存储失败场景
        mock_vector_store = AsyncMock()
        mock_vector_store.add_conversation_round.side_effect = Exception("存储失败")
        service = VectorService(test_user, "test_thread", mock_vector_store)

        # Act & Assert - 执行操作并验证抛出RuntimeError
        with pytest.raises(RuntimeError, match="向量存储操作失败"):
            await service.add_conversation_content(sample_conversation_data)

    @pytest.mark.asyncio
    async def test_search_conversations_empty_query(
        self, vector_service: VectorService
    ) -> None:
        """测试空查询搜索."""
        # Act - 执行空查询和空白字符查询
        result = await vector_service.search_conversations("")

        # Assert - 验证返回空列表
        assert result == []

        # Act - 执行空白字符查询
        result = await vector_service.search_conversations("   ")

        # Assert - 验证返回空列表
        assert result == []

    @pytest.mark.asyncio
    async def test_get_collection_stats_failure(self, test_user: str) -> None:
        """测试获取集合统计失败."""
        mock_vector_store = MagicMock()
        mock_vector_store.get_collection_stats.side_effect = Exception("统计失败")
        service = VectorService(test_user, "test_thread", mock_vector_store)

        result = await service.get_collection_stats()

        assert result["status"] == "failed"
        assert "统计失败" in result["error"]

    @pytest.mark.asyncio
    async def test_health_check_success(self, test_user: str) -> None:
        """测试健康检查成功."""
        mock_vector_store = MagicMock()
        mock_vector_store.get_collection_stats.return_value = {"document_count": 100}
        service = VectorService(test_user, "test_thread", mock_vector_store)

        result = await service.health_check()

        assert result["status"] == "healthy"
        assert result["vector_store_initialized"] is True
        assert result["collection_stats"]["document_count"] == 100
        assert result["error"] is None
