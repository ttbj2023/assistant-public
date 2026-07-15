"""PinnedMemoryBlockService 单元测试.

聚焦容量告警 (不拒绝) 与 CRUD 委托, Mock DAO.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.storage.service.pinned_memory_block_service import (
    MAX_LINES,
    MAX_TOTAL_LENGTH,
    PinnedMemoryBlockService,
)


@pytest.fixture
def service_with_mock_dao() -> tuple[PinnedMemoryBlockService, AsyncMock]:
    """构造 service 并替换其 DAO 为 AsyncMock."""
    service = PinnedMemoryBlockService(session_factory=None)  # type: ignore[arg-type]
    mock_dao = AsyncMock()
    mock_dao.get.return_value = None
    mock_dao.upsert.return_value = None
    mock_dao.delete.return_value = True
    service._dao = mock_dao
    return service, mock_dao


@pytest.mark.asyncio
async def test_set_content_valid(service_with_mock_dao):
    """合法内容(条数与长度内)写入成功, 调用 upsert."""
    service, mock_dao = service_with_mock_dao
    content = "用户位于湖北\n偏好昆剧"
    await service.set_content("u", "t", content)
    mock_dao.upsert.assert_awaited_once_with("u", "t", content)


@pytest.mark.asyncio
async def test_set_content_empty_means_clear(service_with_mock_dao):
    """空串=清空, 仍走 upsert(写入空)."""
    service, mock_dao = service_with_mock_dao
    await service.set_content("u", "t", "")
    mock_dao.upsert.assert_awaited_once_with("u", "t", "")


@pytest.mark.asyncio
async def test_set_content_over_lines_warns_but_writes(service_with_mock_dao, caplog):
    """条数超 MAX_LINES 告警但仍写入(主模型覆写信任, 兜底告警)."""
    service, mock_dao = service_with_mock_dao
    over = "\n".join(f"条目{i}" for i in range(MAX_LINES + 1))
    with caplog.at_level("WARNING"):
        await service.set_content("u", "t", over)
    mock_dao.upsert.assert_awaited_once_with("u", "t", over)
    assert any("条数" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_set_content_over_length_warns_but_writes(service_with_mock_dao, caplog):
    """总长超 MAX_TOTAL_LENGTH 告警但仍写入."""
    service, mock_dao = service_with_mock_dao
    over = "x" * (MAX_TOTAL_LENGTH + 1)
    with caplog.at_level("WARNING"):
        await service.set_content("u", "t", over)
    mock_dao.upsert.assert_awaited_once_with("u", "t", over)
    assert any("总长" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_get_formatted_returns_stored(service_with_mock_dao):
    """get_formatted 返回 DAO 中存储的内容."""
    service, mock_dao = service_with_mock_dao
    mock_record = type("R", (), {"content": "a\nb"})()
    mock_dao.get.return_value = mock_record
    result = await service.get_formatted("u", "t")
    assert result == "a\nb"


@pytest.mark.asyncio
async def test_get_formatted_empty_when_no_record(service_with_mock_dao):
    """无记录时返回空串."""
    service, mock_dao = service_with_mock_dao
    mock_dao.get.return_value = None
    assert await service.get_formatted("u", "t") == ""


def test_check_capacity_within_limits():
    """合法内容返回 True."""
    service = PinnedMemoryBlockService(session_factory=None)  # type: ignore[arg-type]
    assert service.check_capacity("a\nb\nc") is True


def test_check_capacity_over_lines():
    """超条数返回 False."""
    service = PinnedMemoryBlockService(session_factory=None)  # type: ignore[arg-type]
    over = "\n".join(f"条目{i}" for i in range(MAX_LINES + 1))
    assert service.check_capacity(over) is False


def test_check_capacity_over_length():
    """超长度返回 False."""
    service = PinnedMemoryBlockService(session_factory=None)  # type: ignore[arg-type]
    assert service.check_capacity("x" * (MAX_TOTAL_LENGTH + 1)) is False
