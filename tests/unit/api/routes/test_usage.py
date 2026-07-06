"""用量统计路由单元测试."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.api.routes.usage import get_usage_summary, list_usage_events
from src.storage.models.usage import UsageRecord


class _Request:
    def __init__(self, user_id: str = "alice", thread_id: str = "main") -> None:
        self.state = SimpleNamespace(user_id=user_id, thread_id=thread_id)


@pytest.mark.asyncio
async def test_events_default_scope_filters_current_thread() -> None:
    """默认 scope=thread 时应限制当前 API key 对应线程."""
    service = AsyncMock()
    service.list_events.return_value = [
        UsageRecord(
            id=1,
            user_id="alice",
            thread_id="main",
            agent_id="personal",
            operation="llm_chat",
            usage_source="main_chat",
            total_tokens=10,
            accuracy="exact",
        )
    ]

    with patch("src.api.routes.usage.create_usage_service", return_value=service):
        result = await list_usage_events(  # type: ignore[arg-type]
            _Request(),
            scope="thread",
            agent_id=None,
            usage_source=None,
            operation=None,
            start_time=None,
            end_time=None,
            limit=100,
            offset=0,
        )

    query = service.list_events.call_args.args[0]
    assert query.user_id == "alice"
    assert query.thread_id == "main"
    assert result["scope"] == "thread"
    assert result["data"][0]["total_tokens"] == 10


@pytest.mark.asyncio
async def test_summary_user_scope_does_not_filter_thread() -> None:
    """scope=user 时应查询当前用户全量线程."""
    service = AsyncMock()
    service.summarize.return_value = {
        "record_count": 2,
        "total_tokens": 30,
        "by_source": [],
    }

    with patch("src.api.routes.usage.create_usage_service", return_value=service):
        result = await get_usage_summary(  # type: ignore[arg-type]
            _Request(),
            scope="user",
            agent_id=None,
            usage_source=None,
            operation=None,
            start_time=None,
            end_time=None,
        )

    query = service.summarize.call_args.args[0]
    assert query.user_id == "alice"
    assert query.thread_id is None
    assert result["scope"] == "user"
    assert result["data"]["total_tokens"] == 30
