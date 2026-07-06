"""AsyncConversationIndexDAO单元测试.

测试对话索引数据访问对象的核心业务逻辑，Mock所有外部依赖（数据库、会话工厂）。
遵循单元测试设计规范：白盒测试，专注单一功能模块，快速反馈。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.storage.dao.async_conversation_dao import AsyncConversationIndexDAO
from src.storage.models.conversation import ConversationIndex


@pytest.fixture
def mock_session_factory():
    """Mock数据库会话工厂."""
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock()
    factory.return_value.__aexit__ = AsyncMock()
    return factory


@pytest.fixture
def mock_db_ops():
    """Mock数据库操作组件."""
    db_ops = MagicMock()
    db_ops.find_by_filters = AsyncMock(return_value=[])
    db_ops.get_latest = AsyncMock(return_value=[])
    db_ops.bulk_create = AsyncMock(return_value=[])
    db_ops.health_check = AsyncMock(return_value=True)
    db_ops.apply_user_thread_filters = Mock(return_value=Mock())
    db_ops.transaction_scope = MagicMock()
    return db_ops


@pytest.fixture
def conversation_dao(mock_session_factory, mock_db_ops):
    """创建AsyncConversationIndexDAO实例."""
    dao = AsyncConversationIndexDAO(mock_session_factory)
    dao.db_ops = mock_db_ops
    return dao


@pytest.fixture
def mock_session(mock_db_ops):
    """配置 db_ops.session_factory 返回的 Mock session, 用于 session 查询方法."""
    session = AsyncMock()
    session.execute = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    # __aexit__ 返回 False(不抑制异常), 与真实 session 行为一致
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_db_ops.session_factory = factory
    return session


def _scalar_result(items: list) -> MagicMock:
    """构造 execute() 返回的 scalars().all() 链式结果."""
    scalars = MagicMock()
    scalars.all.return_value = items
    result = MagicMock()
    result.scalars.return_value = scalars
    return result


class TestGetByRoundNumber:
    """测试根据轮次号获取对话."""

    @pytest.mark.asyncio
    async def test_get_by_round_number_should_return_conversation(
        self, conversation_dao, mock_db_ops, test_user
    ):
        """测试根据轮次号获取对话：应返回对应的对话索引."""
        # Arrange
        round_number = 5
        expected_conversation = ConversationIndex(
            round_number=round_number,
            user_message="test",
            assistant_response="response",
            user_id=test_user,
            thread_id="test_thread_id",
        )
        mock_db_ops.find_by_filters = AsyncMock(return_value=[expected_conversation])

        # Act
        result = await conversation_dao.get_by_round_number(
            round_number=round_number, user_id=test_user, thread_id="test_thread_id"
        )

        # Assert
        assert result is not None
        assert result.round_number == round_number
        assert result.user_id == test_user

    @pytest.mark.asyncio
    async def test_get_by_round_number_not_found_should_return_none(
        self, conversation_dao, mock_db_ops, test_user
    ):
        """测试根据轮次号获取对话：未找到时应返回None."""
        # Arrange
        mock_db_ops.find_by_filters = AsyncMock(return_value=[])

        # Act
        result = await conversation_dao.get_by_round_number(
            round_number=999, user_id=test_user
        )

        # Assert
        assert result is None


class TestGetLatestConversation:
    """测试获取最新对话."""

    @pytest.mark.asyncio
    async def test_get_latest_conversation_should_return_latest(
        self, conversation_dao, mock_db_ops, test_user
    ):
        """测试获取最新对话：应返回最新轮次的对话."""
        # Arrange
        latest_conv = ConversationIndex(
            round_number=10,
            user_message="latest",
            assistant_response="latest response",
            user_id=test_user,
            thread_id="test_thread_id",
        )
        mock_db_ops.get_latest = AsyncMock(return_value=[latest_conv])

        # Act
        result = await conversation_dao.get_latest_conversation(
            user_id=test_user, thread_id="test_thread_id"
        )

        # Assert
        assert result is not None
        assert result.round_number == 10
        mock_db_ops.get_latest.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_latest_conversation_no_conversations_should_return_none(
        self, conversation_dao, mock_db_ops, test_user
    ):
        """测试获取最新对话：没有对话时应返回None."""
        # Arrange
        mock_db_ops.get_latest = AsyncMock(return_value=[])

        # Act
        result = await conversation_dao.get_latest_conversation(
            user_id=test_user, thread_id="test_thread_id"
        )

        # Assert
        assert result is None


class TestStoreIndexData:
    """测试存储对话索引数据."""

    @pytest.mark.asyncio
    async def test_store_index_data_with_dict_content_should_extract_fields(
        self, conversation_dao, mock_db_ops, test_user
    ):
        """测试存储索引数据：字典内容应提取user_message和assistant_response."""
        # Arrange
        conversation_dao.store_index_data_with_upsert = AsyncMock(
            return_value=ConversationIndex(
                round_number=1,
                user_message="user msg",
                assistant_response="assistant resp",
                user_id=test_user,
                thread_id="test_thread_id",
            )
        )

        # Act
        result = await conversation_dao.store_index_data(
            round_number=1,
            content={
                "user_message": "user msg",
                "assistant_response": "assistant resp",
            },
            user_id=test_user,
            thread_id="test_thread_id",
            agent_id="personal-assistant",
        )

        # Assert
        assert result is not None
        conversation_dao.store_index_data_with_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_index_data_with_plain_content_dict_should_use_as_user_message(
        self, conversation_dao, mock_db_ops, test_user
    ):
        """测试存储索引数据：只有content字段的字典应作为用户消息."""
        # Arrange
        conversation_dao.store_index_data_with_upsert = AsyncMock(
            return_value=ConversationIndex(
                round_number=1,
                user_message="plain content",
                assistant_response="",
                user_id=test_user,
                thread_id="test_thread_id",
            )
        )

        # Act
        result = await conversation_dao.store_index_data(
            round_number=1,
            content={"content": "plain content"},
            user_id=test_user,
            thread_id="test_thread_id",
            agent_id="personal-assistant",
        )

        # Assert
        assert result is not None


class TestBulkCreate:
    """测试批量创建对话索引."""

    @pytest.mark.asyncio
    async def test_bulk_create_should_create_multiple_conversations(
        self, conversation_dao, mock_db_ops, test_user
    ):
        """测试批量创建：应创建多个对话索引记录."""
        # Arrange
        items = [
            {
                "user_id": test_user,
                "thread_id": "test_thread_id",
                "round_number": 1,
                "content": "msg1",
            },
            {
                "user_id": test_user,
                "thread_id": "test_thread_id",
                "round_number": 2,
                "content": "msg2",
            },
        ]
        created = [
            ConversationIndex(
                round_number=item["round_number"],
                user_message=item["content"],
                assistant_response="",
                user_id=test_user,
                thread_id="test_thread_id",
            )
            for item in items
        ]
        mock_db_ops.bulk_create = AsyncMock(return_value=created)

        # Act
        result = await conversation_dao.bulk_create(items)

        # Assert
        assert len(result) == 2
        mock_db_ops.bulk_create.assert_called_once()


class TestHealthCheck:
    """测试健康检查."""

    @pytest.mark.asyncio
    async def test_health_check_should_return_true(self, conversation_dao, mock_db_ops):
        """测试健康检查：应返回True."""
        # Arrange
        mock_db_ops.health_check = AsyncMock(return_value=True)

        # Act
        result = await conversation_dao.health_check()

        # Assert
        assert result is True
        mock_db_ops.health_check.assert_called_once()


class TestGetLatestRoundNumber:
    """测试获取最新轮次号."""

    @pytest.mark.asyncio
    async def test_get_latest_round_number_should_return_round_number(
        self, conversation_dao, test_user
    ):
        """测试获取最新轮次号：应返回最新轮次号."""
        # Arrange
        conversation_dao.get_latest_conversation = AsyncMock(
            return_value=ConversationIndex(
                round_number=5,
                user_message="test",
                assistant_response="test",
                user_id=test_user,
                thread_id="test_thread_id",
            )
        )

        # Act
        result = await conversation_dao.get_latest_round_number(
            user_id=test_user, thread_id="test_thread_id"
        )

        # Assert
        assert result == 5

    @pytest.mark.asyncio
    async def test_get_latest_round_number_no_conversation_should_return_none(
        self, conversation_dao, test_user
    ):
        """测试获取最新轮次号：没有对话时应返回None."""
        # Arrange
        conversation_dao.get_latest_conversation = AsyncMock(return_value=None)

        # Act
        result = await conversation_dao.get_latest_round_number(
            user_id=test_user, thread_id="test_thread_id"
        )

        # Assert
        assert result is None


class TestGetConversationsByRounds:
    """测试根据轮次号列表批量获取对话."""

    @pytest.mark.asyncio
    async def test_get_conversations_by_rounds_empty_list_should_return_empty(
        self, conversation_dao, test_user
    ):
        """测试批量获取：空列表应立即返回空结果."""
        # Act
        result = await conversation_dao.get_conversations_by_rounds(
            user_id=test_user, thread_id="test_thread_id", round_numbers=[]
        )

        # Assert
        assert result == []


class TestStoreIndexDataWithUpsert:
    """测试UPSERT存储索引数据."""

    @pytest.mark.asyncio
    async def test_upsert_should_create_new_record_when_not_exists(
        self, conversation_dao, test_user
    ):
        """测试UPSERT：记录不存在时应创建新记录."""
        # Arrange
        mock_transaction = MagicMock()
        mock_session = MagicMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=Mock(return_value=None))
        )
        mock_session.add = Mock()
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()

        mock_transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_transaction.return_value.__aexit__ = AsyncMock()

        conversation_dao.db_ops.transaction_scope = mock_transaction

        # Act
        result = await conversation_dao.store_index_data_with_upsert(
            round_number=1,
            user_message="test",
            assistant_response="response",
            user_id=test_user,
            thread_id="test_thread_id",
            agent_id="personal-assistant",
        )

        # Assert
        assert result is not None
        mock_session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_upsert_should_update_existing_record_when_exists(
        self, conversation_dao, test_user
    ):
        """测试UPSERT：记录存在时应更新现有记录."""
        # Arrange
        existing = ConversationIndex(
            round_number=1,
            user_message="old message",
            assistant_response="old response",
            user_id=test_user,
            thread_id="test_thread_id",
            topic=None,
            summary=None,
        )

        mock_transaction = MagicMock()
        mock_session = MagicMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=Mock(return_value=existing))
        )
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()

        mock_transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_transaction.return_value.__aexit__ = AsyncMock()

        conversation_dao.db_ops.transaction_scope = mock_transaction

        # Act
        result = await conversation_dao.store_index_data_with_upsert(
            round_number=1,
            user_message="new message",
            assistant_response="new response",
            user_id=test_user,
            thread_id="test_thread_id",
            agent_id="personal-assistant",
        )

        # Assert
        assert result is not None
        assert existing.user_message == "new message"
        assert existing.assistant_response == "new response"

    @pytest.mark.asyncio
    async def test_upsert_should_extract_fields_from_metadata(
        self, conversation_dao, test_user
    ):
        """测试UPSERT：应从metadata中提取topic、summary."""
        # Arrange
        metadata = {
            "topic": "Test Topic",
            "summary": "Test Summary",
        }

        mock_transaction = MagicMock()
        mock_session = MagicMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=Mock(return_value=None))
        )
        mock_session.add = Mock()
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()

        mock_transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_transaction.return_value.__aexit__ = AsyncMock()

        conversation_dao.db_ops.transaction_scope = mock_transaction

        # Act
        result = await conversation_dao.store_index_data_with_upsert(
            round_number=1,
            user_message="test",
            assistant_response="response",
            user_id=test_user,
            thread_id="test_thread_id",
            metadata=metadata,
            agent_id="personal-assistant",
        )

        # Assert
        assert result is not None
        mock_session.add.assert_called_once()


class TestSessionQueryMethods:
    """基于 session.execute 的查询方法."""

    @pytest.mark.asyncio
    async def test_get_conversations_in_range_should_return_list(
        self, conversation_dao, mock_session, test_user
    ):
        """应返回范围内的对话索引列表."""
        conv = ConversationIndex(
            round_number=2,
            user_message="m",
            assistant_response="r",
            user_id=test_user,
            thread_id="test_thread_id",
        )
        mock_session.execute.return_value = _scalar_result([conv])

        result = await conversation_dao.get_conversations_in_range(
            1, 5, test_user, "test_thread_id"
        )

        assert len(result) == 1
        assert result[0].round_number == 2
        mock_session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_conversations_should_return_desc(
        self, conversation_dao, mock_session, test_user
    ):
        """应按轮次降序返回对话列表."""
        mock_session.execute.return_value = _scalar_result([])

        result = await conversation_dao.list_conversations(
            test_user, "test_thread_id", limit=10
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_list_recent_rounds_should_return_round_numbers(
        self, conversation_dao, mock_session, test_user
    ):
        """应返回最近轮次号列表."""
        mock_result = MagicMock()
        mock_result.all.return_value = [(3,), (2,), (1,)]
        mock_session.execute.return_value = mock_result

        result = await conversation_dao.list_recent_rounds(
            test_user, "test_thread_id", limit=3
        )

        assert result == [3, 2, 1]

    @pytest.mark.asyncio
    async def test_get_conversations_by_rounds_should_query(
        self, conversation_dao, mock_session, test_user
    ):
        """非空轮次列表应走 session 查询路径."""
        mock_session.execute.return_value = _scalar_result([])

        result = await conversation_dao.get_conversations_by_rounds(
            test_user, "test_thread_id", [1, 2, 3]
        )

        assert result == []
        mock_session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_conversation_rounds_by_range_should_build_dicts(
        self, conversation_dao, mock_session, test_user
    ):
        """应将对话记录转为包含轮次/消息/时间的字典列表."""
        conv = ConversationIndex(
            round_number=1,
            user_message="u",
            assistant_response="a",
            user_id=test_user,
            thread_id="test_thread_id",
        )
        mock_session.execute.return_value = _scalar_result([conv])

        result = await conversation_dao.get_conversation_rounds_by_range(
            test_user, "test_thread_id", 1, 5
        )

        assert len(result) == 1
        assert result[0]["round_number"] == 1
        assert result[0]["user_message"] == "u"
        assert result[0]["created_at"] is not None


class TestSearchRoundsByKeywords:
    """关键词检索 (LIKE ANY 于 user_message/assistant_response)."""

    @pytest.mark.asyncio
    async def test_empty_terms_should_return_empty_without_query(
        self, conversation_dao, mock_session, test_user
    ):
        """空词列表应直接返回空, 不触发数据库查询."""
        result = await conversation_dao.search_rounds_by_keywords(
            test_user, "test_thread_id", []
        )

        assert result == []
        mock_session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_should_return_round_numbers_from_matched_rows(
        self, conversation_dao, mock_session, test_user
    ):
        """应返回命中行的轮次号列表."""
        mock_result = MagicMock()
        mock_result.all.return_value = [(7,), (3,), (1,)]
        mock_session.execute.return_value = mock_result

        result = await conversation_dao.search_rounds_by_keywords(
            test_user, "test_thread_id", ["Nemo", "疫苗"]
        )

        assert result == [7, 3, 1]
        mock_session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_round_range_should_not_raise(
        self, conversation_dao, mock_session, test_user
    ):
        """传入 round_range 应正常构建带区间过滤的查询."""
        mock_result = MagicMock()
        mock_result.all.return_value = [(5,)]
        mock_session.execute.return_value = mock_result

        result = await conversation_dao.search_rounds_by_keywords(
            test_user, "test_thread_id", ["k8s"], round_range=(1, 10)
        )

        assert result == [5]

    @pytest.mark.asyncio
    async def test_special_like_chars_should_be_escaped_not_crash(
        self, conversation_dao, mock_session, test_user
    ):
        """含 LIKE 特殊字符 (%) 的词应被转义, 不崩溃也不全表命中."""
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute.return_value = mock_result

        result = await conversation_dao.search_rounds_by_keywords(
            test_user, "test_thread_id", ["100%"]
        )

        assert result == []
        mock_session.execute.assert_awaited_once()


class TestFormattedRangeMethods:
    """格式化范围接口(委托给 conversation_formatter)."""

    @pytest.mark.asyncio
    async def test_get_formatted_conversation_range_should_format(
        self, conversation_dao, mock_session, test_user
    ):
        """应查询后委托给 formatter 格式化."""
        conv = ConversationIndex(
            round_number=1,
            user_message="u",
            assistant_response="a",
            user_id=test_user,
            thread_id="test_thread_id",
        )
        mock_session.execute.return_value = _scalar_result([conv])
        conversation_dao.conversation_formatter = Mock()
        conversation_dao.conversation_formatter.format_conversation_range = AsyncMock(
            return_value="**formatted**"
        )

        result = await conversation_dao.get_formatted_conversation_range(
            test_user, "test_thread_id", 1, 5
        )

        assert result == "**formatted**"

    @pytest.mark.asyncio
    async def test_get_formatted_conversation_range_should_return_error_on_exception(
        self, conversation_dao, test_user
    ):
        """查询异常时应返回空字符串, 避免向LLM注入错误文案."""
        conversation_dao.get_conversation_rounds_by_range = AsyncMock(
            side_effect=Exception("fail")
        )

        result = await conversation_dao.get_formatted_conversation_range(
            test_user, "test_thread_id", 1, 5
        )

        assert result == ""

    @pytest.mark.asyncio
    async def test_get_formatted_index_range_should_format(
        self, conversation_dao, mock_session, test_user
    ):
        """应查询后转为索引字典并委托 formatter."""
        conv = ConversationIndex(
            round_number=1,
            user_message="u",
            assistant_response="a",
            user_id=test_user,
            thread_id="test_thread_id",
        )
        mock_session.execute.return_value = _scalar_result([conv])
        conversation_dao.conversation_formatter = Mock()
        conversation_dao.conversation_formatter.format_index_range = AsyncMock(
            return_value="idx-formatted"
        )

        result = await conversation_dao.get_formatted_index_range(
            test_user, "test_thread_id", 1, 5
        )

        assert result == "idx-formatted"

    @pytest.mark.asyncio
    async def test_get_formatted_index_range_should_return_error_on_exception(
        self, conversation_dao, test_user
    ):
        conversation_dao.get_conversations_in_range = AsyncMock(
            side_effect=Exception("fail")
        )

        result = await conversation_dao.get_formatted_index_range(
            test_user, "test_thread_id", 1, 5
        )

        assert result == ""


class TestStoreIndexDataEdgeCases:
    """store_index_data 的异常与空内容分支."""

    @pytest.mark.asyncio
    async def test_store_index_data_should_handle_none_content(
        self, conversation_dao, test_user
    ):
        """content 为 None 时应以空字符串存储."""
        conversation_dao.store_index_data_with_upsert = AsyncMock(
            return_value=ConversationIndex(
                round_number=1,
                user_message="",
                assistant_response="",
                user_id=test_user,
                thread_id="test_thread_id",
            )
        )

        result = await conversation_dao.store_index_data(
            round_number=1,
            content=None,
            user_id=test_user,
            thread_id="test_thread_id",
            agent_id="personal-assistant",
        )

        assert result is not None
        call = conversation_dao.store_index_data_with_upsert.call_args
        assert call.kwargs["user_message"] == ""

    @pytest.mark.asyncio
    async def test_store_index_data_should_reraise_on_upsert_failure(
        self, conversation_dao, test_user
    ):
        """upsert 抛异常时 store_index_data 应重新抛出."""
        conversation_dao.store_index_data_with_upsert = AsyncMock(
            side_effect=RuntimeError("db down")
        )

        with pytest.raises(RuntimeError, match="db down"):
            await conversation_dao.store_index_data(
                round_number=1,
                content={"user_message": "x"},
                user_id=test_user,
                thread_id="test_thread_id",
                agent_id="personal-assistant",
            )
