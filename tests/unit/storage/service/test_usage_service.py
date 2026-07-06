"""UsageService 单元测试."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.storage.models.usage import UsageQuery, UsageRecord, UsageRecordCreate
from src.storage.service.usage_service import UsageService


@pytest.fixture
async def usage_service(tmp_path):
    """创建临时 SQLite UsageService."""
    db_path = tmp_path / "usage.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(UsageRecord.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield UsageService(session_factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_record_usage_should_be_idempotent_by_run_id(usage_service) -> None:
    """相同 run_id + operation 应幂等写入."""
    payload = UsageRecordCreate(
        user_id="alice",
        thread_id="main",
        agent_id="personal",
        operation="llm_chat",
        usage_source="main_chat",
        run_id="run-1",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        accuracy="exact",
    )

    first = await usage_service.record_usage(payload)
    second = await usage_service.record_usage(payload)

    assert first.id == second.id
    events = await usage_service.list_events(UsageQuery(user_id="alice"))
    assert len(events) == 1


@pytest.mark.asyncio
async def test_list_events_should_filter_thread(usage_service) -> None:
    """查询应支持 thread 过滤."""
    await usage_service.record_usage(
        UsageRecordCreate(
            user_id="alice",
            thread_id="main",
            agent_id="personal",
            operation="llm_chat",
            usage_source="main_chat",
            total_tokens=10,
            accuracy="exact",
        )
    )
    await usage_service.record_usage(
        UsageRecordCreate(
            user_id="alice",
            thread_id="other",
            agent_id="personal",
            operation="llm_chat",
            usage_source="main_chat",
            total_tokens=20,
            accuracy="exact",
        )
    )

    events = await usage_service.list_events(
        UsageQuery(user_id="alice", thread_id="main"),
    )

    assert len(events) == 1
    assert events[0].thread_id == "main"


@pytest.mark.asyncio
async def test_summarize_should_aggregate_counts_and_sources(usage_service) -> None:
    """summary 应聚合 token 和 usage_source."""
    await usage_service.record_usage(
        UsageRecordCreate(
            user_id="alice",
            thread_id="main",
            agent_id="personal",
            operation="llm_chat",
            usage_source="main_chat",
            request_count=1,
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            accuracy="exact",
        )
    )
    await usage_service.record_usage(
        UsageRecordCreate(
            user_id="alice",
            thread_id="main",
            agent_id="personal",
            operation="embedding",
            usage_source="main_chat",
            request_count=1,
            input_tokens=7,
            output_tokens=0,
            total_tokens=7,
            accuracy="estimated",
        )
    )

    summary = await usage_service.summarize(UsageQuery(user_id="alice"))

    assert summary["record_count"] == 2
    assert summary["request_count"] == 2
    assert summary["input_tokens"] == 17
    assert summary["output_tokens"] == 5
    assert summary["total_tokens"] == 22
    assert summary["exact_count"] == 1
    assert summary["estimated_count"] == 1
    assert summary["by_source"][0]["usage_source"] == "main_chat"
    assert summary["by_source"][0]["total_tokens"] == 22


@pytest.mark.asyncio
async def test_summarize_empty_result_should_return_zero_counts(usage_service) -> None:
    """无记录时 summary 计数字段应返回 0."""
    summary = await usage_service.summarize(UsageQuery(user_id="alice"))

    assert summary["record_count"] == 0
    assert summary["request_count"] == 0
    assert summary["input_tokens"] == 0
    assert summary["output_tokens"] == 0
    assert summary["total_tokens"] == 0
    assert summary["unknown_count"] == 0
    assert summary["estimated_count"] == 0
    assert summary["exact_count"] == 0
    assert summary["by_source"] == []
