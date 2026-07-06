"""AsyncScheduledMessageDAO 单元测试.

测试定时消息DAO的业务逻辑: 创建, 查询, 状态更新, 过期标记.
Mock外部依赖: AsyncDatabaseOperations, SQLAlchemy session.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.storage.dao.async_scheduled_message_dao import AsyncScheduledMessageDAO
from src.storage.models.scheduled_message import MessageStatus


@pytest.fixture
def mock_session_factory():
    return MagicMock()


@pytest.fixture
def dao(mock_session_factory):
    return AsyncScheduledMessageDAO(mock_session_factory)


def _mock_session_context(dao):
    """创建mock session的上下文管理器."""
    mock_session = AsyncMock()
    dao.session_factory = MagicMock()
    dao.session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    dao.session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


class TestCreateMessage:
    """测试创建消息."""

    @pytest.mark.asyncio
    async def test_delegates_to_db_ops(self, dao):
        mock_result = MagicMock()
        with patch.object(
            dao.db_ops, "create_with_validation", return_value=mock_result
        ):
            result = await dao.create_message(
                message="test",
                send_time=datetime(2026, 6, 1, 8, 0),
                user_id="u1",
                thread_id="t1",
                agent_id="a1",
                channel="wechat",
            )

        assert result == mock_result


class TestGetByMessageId:
    """测试按message_id查询."""

    @pytest.mark.asyncio
    async def test_found(self, dao):
        mock_session = _mock_session_context(dao)
        mock_entry = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_entry
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await dao.get_by_message_id("msg-001")
        assert result == mock_entry

    @pytest.mark.asyncio
    async def test_not_found(self, dao):
        mock_session = _mock_session_context(dao)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await dao.get_by_message_id("nonexist")
        assert result is None


class TestGetPendingMessages:
    """测试查询待发送消息."""

    @pytest.mark.asyncio
    async def test_returns_pending(self, dao):
        mock_session = _mock_session_context(dao)
        entries = [MagicMock()]
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = entries
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await dao.get_pending_messages("u1", "t1", "a1")
        assert result == entries


class TestUpdateStatus:
    """测试更新状态."""

    @pytest.mark.asyncio
    async def test_update_succeeds(self, dao):
        mock_session = _mock_session_context(dao)
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        result = await dao.update_status("msg-001", MessageStatus.SENT, sent_at=datetime.now())
        assert result is True

    @pytest.mark.asyncio
    async def test_update_not_found(self, dao):
        mock_session = _mock_session_context(dao)
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        result = await dao.update_status("nonexist", MessageStatus.SENT)
        assert result is False


class TestMarkExpiredAsMissed:
    """测试标记过期消息."""

    @pytest.mark.asyncio
    async def test_marks_expired(self, dao):
        mock_session = _mock_session_context(dao)
        mock_result = MagicMock()
        mock_result.rowcount = 3
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        result = await dao.mark_expired_as_missed(datetime.now())
        assert result == 3


class TestCountPending:
    """测试统计待发送数量."""

    @pytest.mark.asyncio
    async def test_returns_count(self, dao):
        mock_session = _mock_session_context(dao)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 5
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await dao.count_pending("u1", "t1", "a1")
        assert result == 5

    @pytest.mark.asyncio
    async def test_returns_zero_when_none(self, dao):
        mock_session = _mock_session_context(dao)
        mock_result = MagicMock()
        mock_result.scalar.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await dao.count_pending("u1", "t1", "a1")
        assert result == 0


class TestHealthCheck:
    """测试健康检查."""

    @pytest.mark.asyncio
    async def test_delegates_to_db_ops(self, dao):
        with patch.object(dao.db_ops, "health_check", return_value=True):
            result = await dao.health_check()
        assert result is True
