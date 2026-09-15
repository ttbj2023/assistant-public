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

        result = await dao.update_status(
            "msg-001", MessageStatus.SENT, sent_at=datetime.now()
        )
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

    @pytest.mark.asyncio
    async def test_last_error_written_when_provided(self, dao):
        """传入 last_error 字符串时应写入该列."""
        mock_session = _mock_session_context(dao)
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        await dao.update_status(
            "msg-001", MessageStatus.FAILED, last_error="网关投递失败: prepare failed"
        )

        stmt = mock_session.execute.call_args[0][0]
        params = stmt.compile().params
        assert params.get("last_error") == "网关投递失败: prepare failed"

    @pytest.mark.asyncio
    async def test_last_error_column_untouched_when_unset(self, dao):
        """未传 last_error 时不应更新该列 (区分清空与不更新)."""
        mock_session = _mock_session_context(dao)
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        await dao.update_status("msg-001", MessageStatus.CANCELLED)

        stmt = mock_session.execute.call_args[0][0]
        params = stmt.compile().params
        assert "last_error" not in params

    @pytest.mark.asyncio
    async def test_last_error_cleared_by_explicit_none(self, dao):
        """显式传 None 应更新该列为 NULL (发送成功清空历史错误)."""
        mock_session = _mock_session_context(dao)
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        await dao.update_status("msg-001", MessageStatus.SENT, last_error=None)

        stmt = mock_session.execute.call_args[0][0]
        params = stmt.compile().params
        assert "last_error" in params


class TestGetFailedMessages:
    """测试查询失败消息."""

    @pytest.mark.asyncio
    async def test_returns_failed(self, dao):
        mock_session = _mock_session_context(dao)
        entries = [MagicMock()]
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = entries
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await dao.get_failed_messages("u1", "t1", "a1")
        assert result == entries


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


class TestGetPendingByRelatedEvent:
    """测试按关联日程查询影子消息 (跨线程级联入口)."""

    @pytest.mark.asyncio
    async def test_returns_matching_pending(self, dao):
        mock_session = _mock_session_context(dao)
        mock_msg = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_msg]
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await dao.get_pending_by_related_event("u1", 42)
        assert result == [mock_msg]
        # 断言过滤条件包含 related_event_id 与 PENDING 状态
        stmt = mock_session.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "related_event_id" in compiled
        assert "PENDING" in compiled

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_shadow(self, dao):
        mock_session = _mock_session_context(dao)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await dao.get_pending_by_related_event("u1", 99)
        assert result == []


class TestUpdateSendTime:
    """测试顺延影子消息发送时间 (改期级联)."""

    @pytest.mark.asyncio
    async def test_update_succeeds(self, dao):
        mock_session = _mock_session_context(dao)
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        new_time = datetime(2026, 6, 2, 7, 0)
        result = await dao.update_send_time("msg-001", new_time)
        assert result is True
        stmt = mock_session.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "send_time" in compiled

    @pytest.mark.asyncio
    async def test_update_not_found(self, dao):
        mock_session = _mock_session_context(dao)
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        result = await dao.update_send_time("nonexist", datetime(2026, 6, 2, 7, 0))
        assert result is False
