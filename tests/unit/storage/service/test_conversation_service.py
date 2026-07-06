"""ConversationService单元测试.

测试对话服务的业务逻辑, 包括对话创建、轮次管理、格式化等。
遵循单元测试设计规范: Mock外部依赖, 测试业务逻辑。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.storage.models.conversation import ConversationIndex
from src.storage.service.conversation_service import ConversationService


@pytest.fixture
def mock_session_factory():
    """模拟SQLAlchemy会话工厂, 正确支持嵌套async with."""
    mock_session = AsyncMock()
    # session.begin() 返回的对象需支持 async with
    mock_session.begin = MagicMock(return_value=AsyncMock())

    class AsyncSessionMock:
        def __init__(self):
            self._session = mock_session

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, *args):
            pass

        def begin(self):
            class TransactionMock:
                async def __aenter__(self):
                    return AsyncMock()

                async def __aexit__(self, *args):
                    pass

            return TransactionMock()

    factory = MagicMock()
    factory.return_value = AsyncSessionMock()
    return factory


@pytest.fixture
def conversation_service(mock_session_factory):
    """创建ConversationService实例."""
    return ConversationService(mock_session_factory)


@pytest.fixture
def mock_conversation_index():
    """创建标准的ConversationIndex对象."""
    return ConversationIndex(
        id=1,
        round_number=1,
        user_id="test_user",
        thread_id="test_thread",
        agent_id="personal-assistant",
        user_message="你好",
        assistant_response="你好！有什么可以帮你的？",
        content={
            "user_message": "你好",
            "assistant_response": "你好！有什么可以帮你的？",
        },
    )


class TestConversationServiceCreate:
    """测试create_conversation方法."""

    @pytest.mark.asyncio
    async def test_create_conversation_should_store_data(
        self, conversation_service, mock_conversation_index, test_user
    ):
        """测试创建对话: 有效数据应成功存储."""
        conversation_service.conversation_dao.store_index_data = AsyncMock(
            return_value=mock_conversation_index
        )
        conversation_service.allocate_round_number = AsyncMock(return_value=1)

        result = await conversation_service.create_conversation(
            user_message="你好",
            assistant_response="你好！",
            user_id=test_user,
            thread_id="test_thread",
            agent_id="personal-assistant",
        )

        assert result is not None
        assert result.round_number == 1
        conversation_service.conversation_dao.store_index_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_conversation_should_auto_allocate_round(
        self, conversation_service, mock_conversation_index, test_user
    ):
        """测试创建对话: 无预分配轮次号时应自动分配."""
        conversation_service.conversation_dao.store_index_data = AsyncMock(
            return_value=mock_conversation_index
        )
        conversation_service.allocate_round_number = AsyncMock(return_value=5)

        await conversation_service.create_conversation(
            user_message="测试消息",
            assistant_response="测试回复",
            user_id=test_user,
            thread_id="test_thread",
            agent_id="personal-assistant",
            round_number=None,
        )

        conversation_service.allocate_round_number.assert_called_once_with(
            test_user, "test_thread"
        )

    @pytest.mark.asyncio
    async def test_create_conversation_should_use_preallocated_round(
        self, conversation_service, mock_conversation_index, test_user
    ):
        """测试创建对话: 有预分配轮次号时应直接使用."""
        conversation_service.conversation_dao.store_index_data = AsyncMock(
            return_value=mock_conversation_index
        )

        await conversation_service.create_conversation(
            user_message="测试消息",
            assistant_response="测试回复",
            user_id=test_user,
            thread_id="test_thread",
            agent_id="personal-assistant",
            round_number=10,
        )

        call_kwargs = conversation_service.conversation_dao.store_index_data.call_args[
            1
        ]
        assert call_kwargs["round_number"] == 10

    @pytest.mark.asyncio
    async def test_create_conversation_should_reject_empty_user_message(
        self, conversation_service, test_user
    ):
        """测试创建对话: 空用户消息应抛出ValueError."""
        with pytest.raises(ValueError, match="用户消息不能为空"):
            await conversation_service.create_conversation(
                user_message="  ",
                assistant_response="回复",
                user_id=test_user,
                thread_id="test_thread",
                agent_id="personal-assistant",
            )

    @pytest.mark.asyncio
    async def test_create_conversation_should_reject_empty_assistant_response(
        self, conversation_service, test_user
    ):
        """测试创建对话: 空助手回复应抛出ValueError."""
        with pytest.raises(ValueError, match="助手回复不能为空"):
            await conversation_service.create_conversation(
                user_message="消息",
                assistant_response="  ",
                user_id=test_user,
                thread_id="test_thread",
                agent_id="personal-assistant",
            )

    @pytest.mark.asyncio
    async def test_create_conversation_should_set_default_metadata(
        self, conversation_service, mock_conversation_index, test_user
    ):
        """测试创建对话: 无metadata时应设置默认值."""
        conversation_service.conversation_dao.store_index_data = AsyncMock(
            return_value=mock_conversation_index
        )
        conversation_service.allocate_round_number = AsyncMock(return_value=1)

        await conversation_service.create_conversation(
            user_message="消息",
            assistant_response="回复",
            user_id=test_user,
            thread_id="test_thread",
            agent_id="personal-assistant",
            metadata=None,
        )

        call_kwargs = conversation_service.conversation_dao.store_index_data.call_args[
            1
        ]
        assert "timestamp" in call_kwargs["metadata"]
        assert call_kwargs["metadata"]["message_count"] == 2


class TestConversationServiceGetByRound:
    """测试get_conversation_by_round方法."""

    @pytest.mark.asyncio
    async def test_get_conversation_should_return_conversation(
        self, conversation_service, mock_conversation_index, test_user
    ):
        """测试获取对话: 存在时应返回对话记录."""
        conversation_service.conversation_dao.get_by_round_number = AsyncMock(
            return_value=mock_conversation_index
        )

        result = await conversation_service.get_conversation_by_round(
            user_id=test_user,
            thread_id="test_thread",
            round_number=1,
        )

        assert result is not None
        assert result.round_number == 1

    @pytest.mark.asyncio
    async def test_get_conversation_should_return_none_when_not_found(
        self, conversation_service, test_user
    ):
        """测试获取对话: 不存在时应返回None."""
        conversation_service.conversation_dao.get_by_round_number = AsyncMock(
            return_value=None
        )

        result = await conversation_service.get_conversation_by_round(
            user_id=test_user,
            thread_id="test_thread",
            round_number=999,
        )

        assert result is None


class TestConversationServiceFormattedRange:
    """测试格式化方法."""

    @pytest.mark.asyncio
    async def test_get_formatted_index_range_should_delegate(
        self, conversation_service, test_user
    ):
        """测试格式化索引: 应委托给DAO."""
        conversation_service.conversation_dao.get_formatted_index_range = AsyncMock(
            return_value="## 索引\n1. 对话1"
        )

        result = await conversation_service.get_formatted_index_range(
            user_id=test_user,
            thread_id="test_thread",
            start_round=1,
            end_round=5,
        )

        assert "索引" in result
        conversation_service.conversation_dao.get_formatted_index_range.assert_called_once()


class TestConversationServiceList:
    """测试list_conversations方法."""

    @pytest.mark.asyncio
    async def test_list_conversations_should_sort_by_round_desc(
        self, conversation_service, test_user
    ):
        """测试列出对话: 应按轮次号降序排列."""
        conversations = [
            ConversationIndex(
                id=i,
                round_number=i,
                user_id=test_user,
                thread_id="test_thread",
                agent_id="personal-assistant",
                user_message=f"消息{i}",
                assistant_response=f"回复{i}",
                content={"user_message": f"消息{i}", "assistant_response": f"回复{i}"},
            )
            for i in [1, 3, 2]
        ]
        # DAO 层已按 round_number 降序返回
        sorted_conversations = sorted(
            conversations, key=lambda c: c.round_number, reverse=True
        )
        conversation_service.conversation_dao.list_conversations = AsyncMock(
            return_value=sorted_conversations
        )

        result = await conversation_service.list_conversations(
            user_id=test_user,
            thread_id="test_thread",
        )

        assert len(result) == 3
        assert result[0].round_number == 3
        assert result[1].round_number == 2
        assert result[2].round_number == 1


class TestConversationServiceHealthCheck:
    """测试健康检查方法."""

    @pytest.mark.asyncio
    async def test_health_check_should_return_healthy(self, conversation_service):
        """测试健康检查: 数据库正常时应返回healthy."""
        mock_execute_result = Mock()
        mock_execute_result.scalar.return_value = 0

        mock_session = conversation_service.session_factory.return_value._session
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        result = await conversation_service.health_check()

        assert result["status"] == "healthy"
        assert result["database_connected"] is True

    @pytest.mark.asyncio
    async def test_health_check_should_return_unhealthy_on_connection_error(
        self, conversation_service
    ):
        """测试健康检查: 连接失败时应返回unhealthy."""
        factory = MagicMock()

        class FailingSession:
            async def __aenter__(self):
                raise Exception("connection refused")

            async def __aexit__(self, *args):
                pass

        factory.return_value = FailingSession()
        conversation_service.session_factory = factory

        result = await conversation_service.health_check()

        assert result["status"] == "unhealthy"
        assert result["database_connected"] is False


class TestConversationServiceGetStatistics:
    """测试_get_conversation_statistics方法."""

    @pytest.mark.asyncio
    async def test_get_statistics_should_return_empty_when_exception(
        self, conversation_service
    ):
        """测试统计: 异常时应返回空统计."""
        factory = MagicMock()

        class FailingSession:
            async def __aenter__(self):
                raise Exception("DB error")

            async def __aexit__(self, *args):
                pass

        factory.return_value = FailingSession()
        conversation_service.session_factory = factory

        result = await conversation_service._get_conversation_statistics()

        assert result["total_conversations"] == 0
        assert result["active_threads"] == 0
        assert result["latest_conversation_time"] is None


class TestConversationServiceAllocateRound:
    """测试allocate_round_number方法 - 覆盖session交互."""

    @pytest.mark.asyncio
    async def test_allocate_round_should_return_max_plus_one(
        self, conversation_service, test_user
    ):
        """测试轮次号分配: 应返回当前最大值+1."""
        mock_execute_result = Mock()
        mock_execute_result.scalar.return_value = 5

        mock_session = conversation_service.session_factory.return_value._session
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        result = await conversation_service.allocate_round_number(
            test_user, "test_thread"
        )

        assert result == 6

    @pytest.mark.asyncio
    async def test_allocate_round_should_return_one_when_no_conversations(
        self, conversation_service, test_user
    ):
        """测试轮次号分配: 无对话时应返回1."""
        mock_execute_result = Mock()
        mock_execute_result.scalar.return_value = 0

        mock_session = conversation_service.session_factory.return_value._session
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        result = await conversation_service.allocate_round_number(
            test_user, "test_thread"
        )

        assert result == 1


class TestConversationServiceGetLatestRound:
    """测试get_latest_round_number方法."""

    @pytest.mark.asyncio
    async def test_get_latest_round_should_return_zero_when_empty(
        self, conversation_service, test_user
    ):
        """测试获取最新轮次号: 无对话时应返回0."""
        mock_execute_result = Mock()
        mock_execute_result.scalar.return_value = 0

        mock_session = conversation_service.session_factory.return_value._session
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        result = await conversation_service.get_latest_round_number(
            test_user, "test_thread"
        )

        assert result == 0

    @pytest.mark.asyncio
    async def test_get_latest_round_should_return_max_round(
        self, conversation_service, test_user
    ):
        """测试获取最新轮次号: 应返回最大轮次号."""
        mock_execute_result = Mock()
        mock_execute_result.scalar.return_value = 10

        mock_session = conversation_service.session_factory.return_value._session
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        result = await conversation_service.get_latest_round_number(
            test_user, "test_thread"
        )

        assert result == 10


class TestConversationServiceExceptionPaths:
    """DAO异常时的错误处理路径测试."""

    @pytest.mark.asyncio
    async def test_allocate_round_number_with_dao_error_should_re_raise(
        self, conversation_service, test_user
    ):
        """分配轮次号：DAO异常时应记录日志并重新抛出."""
        # Arrange
        mock_session = conversation_service.session_factory.return_value._session
        mock_session.execute = AsyncMock(side_effect=Exception("DB error"))

        # Act & Assert
        with pytest.raises(Exception, match="DB error"):
            await conversation_service.allocate_round_number(test_user, "test_thread")

    @pytest.mark.asyncio
    async def test_get_latest_round_number_with_dao_error_should_re_raise(
        self, conversation_service, test_user
    ):
        """获取最新轮次号：DAO异常时应记录日志并重新抛出."""
        # Arrange
        mock_session = conversation_service.session_factory.return_value._session
        mock_session.execute = AsyncMock(side_effect=Exception("DB error"))

        # Act & Assert
        with pytest.raises(Exception, match="DB error"):
            await conversation_service.get_latest_round_number(test_user, "test_thread")

    @pytest.mark.asyncio
    async def test_get_conversation_by_round_with_dao_error_should_re_raise(
        self, conversation_service, test_user
    ):
        """获取对话：DAO异常时应记录日志并重新抛出."""
        # Arrange
        conversation_service.conversation_dao.get_by_round_number = AsyncMock(
            side_effect=Exception("DB error"),
        )

        # Act & Assert
        with pytest.raises(Exception, match="DB error"):
            await conversation_service.get_conversation_by_round(
                user_id=test_user, thread_id="test_thread", round_number=1,
            )

    @pytest.mark.asyncio
    async def test_get_formatted_index_range_with_dao_error_should_re_raise(
        self, conversation_service, test_user
    ):
        """获取格式化索引：DAO异常时应记录日志并重新抛出."""
        # Arrange
        conversation_service.conversation_dao.get_formatted_index_range = AsyncMock(
            side_effect=Exception("DB error"),
        )

        # Act & Assert
        with pytest.raises(Exception, match="DB error"):
            await conversation_service.get_formatted_index_range(
                user_id=test_user, thread_id="test_thread",
                start_round=1, end_round=5,
            )

    @pytest.mark.asyncio
    async def test_list_conversations_with_dao_error_should_re_raise(
        self, conversation_service, test_user
    ):
        """列出对话：DAO异常时应记录日志并重新抛出."""
        # Arrange
        conversation_service.conversation_dao.list_conversations = AsyncMock(
            side_effect=Exception("DB error"),
        )

        # Act & Assert
        with pytest.raises(Exception, match="DB error"):
            await conversation_service.list_conversations(
                user_id=test_user, thread_id="test_thread",
            )


class TestConversationServiceListRecentRounds:
    """测试list_recent_rounds方法."""

    @pytest.mark.asyncio
    async def test_list_recent_rounds_should_delegate_to_dao(
        self, conversation_service, test_user
    ):
        """列出最近轮次号：应委托给DAO."""
        # Arrange
        conversation_service.conversation_dao.list_recent_rounds = AsyncMock(
            return_value=[5, 3, 1],
        )

        # Act
        result = await conversation_service.list_recent_rounds(
            user_id=test_user, thread_id="test_thread", limit=10,
        )

        # Assert
        assert result == [5, 3, 1]
        conversation_service.conversation_dao.list_recent_rounds.assert_awaited_once_with(
            test_user, "test_thread", limit=10,
        )
