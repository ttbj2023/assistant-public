"""UserRequirementService单元测试.

聚焦限额校验 (≤10 行 / ≤500 字) 与 set/clear 委托, Mock DAO.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.storage.service.user_requirement_service import (
    MAX_LINES,
    MAX_TOTAL_LENGTH,
    UserRequirementService,
)


@pytest.fixture
def service_with_mock_dao() -> tuple[UserRequirementService, AsyncMock]:
    """构造 service 并替换其 DAO 为 AsyncMock."""
    service = UserRequirementService(session_factory=None)  # type: ignore[arg-type]
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
    content = "回复简洁\n用中文"  # 2行
    await service.set_content("u", "t", content)
    mock_dao.upsert.assert_awaited_once_with("u", "t", content)


@pytest.mark.asyncio
async def test_set_content_empty_means_clear(service_with_mock_dao):
    """空串=清空, 仍走 upsert(写入空)."""
    service, mock_dao = service_with_mock_dao
    await service.set_content("u", "t", "")
    mock_dao.upsert.assert_awaited_once_with("u", "t", "")


@pytest.mark.asyncio
async def test_set_content_over_lines_rejected(service_with_mock_dao):
    """条数超 MAX_LINES 拒绝, 不写库."""
    service, mock_dao = service_with_mock_dao
    over = "\n".join(f"要求{i}" for i in range(MAX_LINES + 1))
    with pytest.raises(ValueError, match="条数"):
        await service.set_content("u", "t", over)
    mock_dao.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_content_over_length_rejected(service_with_mock_dao):
    """总长超 MAX_TOTAL_LENGTH 拒绝."""
    service, mock_dao = service_with_mock_dao
    over = "x" * (MAX_TOTAL_LENGTH + 1)  # 单行但超长
    with pytest.raises(ValueError, match="总长"):
        await service.set_content("u", "t", over)
    mock_dao.upsert.assert_not_awaited()


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
