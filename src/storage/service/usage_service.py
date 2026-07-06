"""模型用量统计业务服务."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.storage.dao.async_usage_dao import AsyncUsageDAO
from src.storage.models.usage import UsageQuery, UsageRecord, UsageRecordCreate


class UsageService:
    """模型用量统计服务."""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self.usage_dao = AsyncUsageDAO(session_factory)

    async def record_usage(self, data: UsageRecordCreate) -> UsageRecord:
        """写入一条用量记录."""
        return await self.usage_dao.create_record(data)

    async def list_events(self, query: UsageQuery) -> list[UsageRecord]:
        """查询用量事件."""
        return await self.usage_dao.list_records(query)

    async def summarize(self, query: UsageQuery) -> dict[str, Any]:
        """汇总用量."""
        summary = await self.usage_dao.summarize(query)
        by_source = await self.usage_dao.summarize_by_source(query)
        summary["by_source"] = by_source
        return summary


__all__ = ["UsageService"]
