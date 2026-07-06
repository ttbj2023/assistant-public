"""用量统计 DAO."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import Select, case, desc, func, select

from src.storage.dao.database_operations import AsyncDatabaseOperations
from src.storage.models.usage import UsageQuery, UsageRecord, UsageRecordCreate


class AsyncUsageDAO:
    """用量统计数据访问对象."""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self.session_factory = session_factory
        self.db_ops = AsyncDatabaseOperations(session_factory, UsageRecord)

    async def create_record(self, data: UsageRecordCreate) -> UsageRecord:
        """创建用量记录, 对 run_id/external_job_id 做幂等写入."""
        existing = await self._find_existing(data)
        if existing is not None:
            return existing

        values = data.model_dump(exclude={"raw_usage", "metadata"})
        values["raw_usage_json"] = self._dump_json(data.raw_usage)
        values["metadata_json"] = self._dump_json(data.metadata)
        return await self.db_ops.create(**values)

    async def list_records(self, query: UsageQuery) -> list[UsageRecord]:
        """按条件查询用量记录."""
        async with self.session_factory() as session:
            stmt = self._apply_filters(select(UsageRecord), query)
            stmt = stmt.order_by(desc(UsageRecord.created_at), desc(UsageRecord.id))
            stmt = stmt.limit(query.limit).offset(query.offset)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def summarize(self, query: UsageQuery) -> dict[str, Any]:
        """按条件聚合用量."""
        async with self.session_factory() as session:
            stmt = select(
                func.count(UsageRecord.id).label("record_count"),
                func.coalesce(func.sum(UsageRecord.request_count), 0).label(
                    "request_count",
                ),
                func.coalesce(func.sum(UsageRecord.input_tokens), 0).label(
                    "input_tokens",
                ),
                func.coalesce(func.sum(UsageRecord.output_tokens), 0).label(
                    "output_tokens",
                ),
                func.coalesce(func.sum(UsageRecord.total_tokens), 0).label(
                    "total_tokens",
                ),
                func.coalesce(func.sum(UsageRecord.cache_read_tokens), 0).label(
                    "cache_read_tokens",
                ),
                func.coalesce(func.sum(UsageRecord.cache_creation_tokens), 0).label(
                    "cache_creation_tokens",
                ),
                func.coalesce(func.sum(UsageRecord.reasoning_tokens), 0).label(
                    "reasoning_tokens",
                ),
                func.coalesce(
                    func.sum(case((UsageRecord.accuracy == "unknown", 1), else_=0)),
                    0,
                ).label("unknown_count"),
                func.coalesce(
                    func.sum(case((UsageRecord.accuracy == "estimated", 1), else_=0)),
                    0,
                ).label("estimated_count"),
                func.coalesce(
                    func.sum(case((UsageRecord.accuracy == "exact", 1), else_=0)),
                    0,
                ).label("exact_count"),
            )
            stmt = self._apply_filters(stmt, query)
            row = (await session.execute(stmt)).mappings().one()
            return dict(row)

    async def summarize_by_source(self, query: UsageQuery) -> list[dict[str, Any]]:
        """按 usage_source 聚合用量."""
        async with self.session_factory() as session:
            stmt = select(
                UsageRecord.usage_source.label("usage_source"),
                func.count(UsageRecord.id).label("record_count"),
                func.coalesce(func.sum(UsageRecord.request_count), 0).label(
                    "request_count",
                ),
                func.coalesce(func.sum(UsageRecord.input_tokens), 0).label(
                    "input_tokens",
                ),
                func.coalesce(func.sum(UsageRecord.output_tokens), 0).label(
                    "output_tokens",
                ),
                func.coalesce(func.sum(UsageRecord.total_tokens), 0).label(
                    "total_tokens",
                ),
            )
            stmt = self._apply_filters(stmt, query)
            stmt = stmt.group_by(UsageRecord.usage_source).order_by(
                UsageRecord.usage_source,
            )
            result = await session.execute(stmt)
            return [dict(row) for row in result.mappings().all()]

    async def _find_existing(
        self,
        data: UsageRecordCreate,
    ) -> UsageRecord | None:
        async with self.session_factory() as session:
            stmt = select(UsageRecord).where(UsageRecord.user_id == data.user_id)

            if data.run_id:
                stmt = stmt.where(
                    UsageRecord.run_id == data.run_id,
                    UsageRecord.operation == data.operation,
                )
            elif data.external_job_id:
                stmt = stmt.where(
                    UsageRecord.external_job_id == data.external_job_id,
                    UsageRecord.operation == data.operation,
                )
            else:
                return None

            result = await session.execute(stmt.limit(1))
            return result.scalar_one_or_none()

    @staticmethod
    def _apply_filters(
        stmt: Select[tuple[UsageRecord]],
        query: UsageQuery,
    ) -> Select[tuple[UsageRecord]]:
        stmt = stmt.where(UsageRecord.user_id == query.user_id)
        if query.thread_id is not None:
            stmt = stmt.where(UsageRecord.thread_id == query.thread_id)
        if query.agent_id is not None:
            stmt = stmt.where(UsageRecord.agent_id == query.agent_id)
        if query.usage_source is not None:
            stmt = stmt.where(UsageRecord.usage_source == query.usage_source)
        if query.operation is not None:
            stmt = stmt.where(UsageRecord.operation == query.operation)
        if query.start_time is not None:
            stmt = stmt.where(UsageRecord.created_at >= query.start_time)
        if query.end_time is not None:
            stmt = stmt.where(UsageRecord.created_at <= query.end_time)
        return stmt

    @staticmethod
    def _dump_json(value: dict | None) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, default=str)


__all__ = ["AsyncUsageDAO"]
