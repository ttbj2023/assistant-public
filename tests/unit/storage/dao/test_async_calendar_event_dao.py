"""AsyncCalendarEventDAO 单元测试.

测试用户级日历 DAO 的业务逻辑: CRUD / 时间范围重叠查询 / UID 查询.
Mock 外部依赖: AsyncDatabaseOperations, SQLAlchemy session.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.storage.dao.async_calendar_event_dao import AsyncCalendarEventDAO
from src.storage.models.calendar_event import CalendarEvent


@pytest.fixture
def mock_session_factory():
    return MagicMock()


@pytest.fixture
def dao(mock_session_factory):
    return AsyncCalendarEventDAO(mock_session_factory)


@pytest.fixture
def sample_event():
    start = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    return CalendarEvent(
        title="团队周会",
        start_time=start,
        end_time=start + timedelta(hours=1),
        user_id="user1",
    )


class TestCreateEvent:
    """测试创建日程."""

    @pytest.mark.asyncio
    async def test_create_event_returns_persisted(self, dao, sample_event):
        with patch.object(
            dao.db_ops, "create", return_value=sample_event
        ) as mock_create:
            result = await dao.create_event(
                title="团队周会",
                user_id="user1",
                start_time=sample_event.start_time,
                end_time=sample_event.end_time,
            )
        assert result == sample_event
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_event_missing_required_raises(self, dao):
        with patch.object(
            dao.db_ops,
            "create",
            side_effect=ValueError("必填字段缺失: title"),
        ):
            with pytest.raises(ValueError, match="title"):
                await dao.create_event(user_id="user1")


class TestGetEventById:
    """测试按 ID 查询."""

    @pytest.mark.asyncio
    async def test_found(self, dao, sample_event):
        with patch.object(dao.db_ops, "get_by_id", return_value=sample_event):
            result = await dao.get_event_by_id(1)
        assert result == sample_event

    @pytest.mark.asyncio
    async def test_not_found(self, dao):
        with patch.object(dao.db_ops, "get_by_id", return_value=None):
            result = await dao.get_event_by_id(999)
        assert result is None


class TestFindByUid:
    """测试按 event_uid 查询."""

    @pytest.mark.asyncio
    async def test_found(self, dao, sample_event):
        with patch.object(dao.db_ops, "find_by_filters", return_value=[sample_event]):
            result = await dao.find_by_uid("evt_abc12345")
        assert result == sample_event

    @pytest.mark.asyncio
    async def test_not_found(self, dao):
        with patch.object(dao.db_ops, "find_by_filters", return_value=[]):
            result = await dao.find_by_uid("evt_nonexist0")
        assert result is None


class TestUpdateDelete:
    """测试更新与删除."""

    @pytest.mark.asyncio
    async def test_update_event(self, dao, sample_event):
        with patch.object(
            dao.db_ops, "update", return_value=sample_event
        ) as mock_update:
            result = await dao.update_event(1, {"title": "改名"})
        assert result == sample_event
        mock_update.assert_called_once_with(1, {"title": "改名"})

    @pytest.mark.asyncio
    async def test_delete_event(self, dao):
        with patch.object(dao.db_ops, "delete_by_id", return_value=True) as mock_delete:
            result = await dao.delete_event(1)
        assert result is True
        mock_delete.assert_called_once_with(1)


class TestListByTimeRange:
    """测试时间范围重叠查询."""

    @pytest.mark.asyncio
    async def test_query_overlapping_events(self, dao, sample_event):
        """应返回与窗口重叠的事件, 过滤条件含 user_id 与状态."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sample_event]
        mock_session.execute = AsyncMock(return_value=mock_result)

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        dao.session_factory.return_value = ctx

        window_start = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)
        window_end = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
        result = await dao.list_by_time_range("user1", window_start, window_end)

        assert result == [sample_event]
        mock_session.execute.assert_called_once()
        # 验证 SQL 包含重叠语义与重复事件豁免条件
        compiled = mock_session.execute.call_args.args[0]
        sql_text = str(compiled)
        assert "user_id" in sql_text
        assert "start_time" in sql_text
        assert "end_time" in sql_text
        assert "recurrence_rule" in sql_text
