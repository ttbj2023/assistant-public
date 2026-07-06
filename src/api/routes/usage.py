"""用量统计只读 API."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status

from src.storage.models.usage import UsageQuery
from src.storage.service import create_usage_service

router = APIRouter(tags=["usage"])


@router.get("/v1/usage/events")
async def list_usage_events(
    request: Request,
    scope: Literal["thread", "user"] = Query(default="thread"),
    agent_id: str | None = Query(default=None),
    usage_source: str | None = Query(default=None),
    operation: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """查询用量事件明细."""
    user_id, thread_id = _get_request_identity(request)
    service = await create_usage_service(user_id)
    query = UsageQuery(
        user_id=user_id,
        thread_id=thread_id if scope == "thread" else None,
        agent_id=agent_id,
        usage_source=usage_source,
        operation=operation,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        offset=offset,
    )
    records = await service.list_events(query)
    return {
        "object": "list",
        "scope": scope,
        "data": [_record_to_dict(record) for record in records],
        "limit": limit,
        "offset": offset,
    }


@router.get("/v1/usage/summary")
async def get_usage_summary(
    request: Request,
    scope: Literal["thread", "user"] = Query(default="thread"),
    agent_id: str | None = Query(default=None),
    usage_source: str | None = Query(default=None),
    operation: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
) -> dict[str, Any]:
    """查询用量聚合."""
    user_id, thread_id = _get_request_identity(request)
    service = await create_usage_service(user_id)
    query = UsageQuery(
        user_id=user_id,
        thread_id=thread_id if scope == "thread" else None,
        agent_id=agent_id,
        usage_source=usage_source,
        operation=operation,
        start_time=start_time,
        end_time=end_time,
    )
    summary = await service.summarize(query)
    return {
        "object": "usage.summary",
        "scope": scope,
        "data": summary,
    }


def _get_request_identity(request: Request) -> tuple[str, str]:
    user_id = getattr(request.state, "user_id", None)
    thread_id = getattr(request.state, "thread_id", None)
    if not user_id or not thread_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication middleware error: missing user information",
        )
    return str(user_id), str(thread_id)


def _record_to_dict(record: Any) -> dict[str, Any]:
    data = record.model_dump()
    if record.created_at:
        data["created_at"] = record.created_at.isoformat()
    data["raw_usage"] = _load_json(record.raw_usage_json)
    data["metadata"] = _load_json(record.metadata_json)
    data.pop("raw_usage_json", None)
    data.pop("metadata_json", None)
    return data


def _load_json(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        return {"value": value}


__all__ = ["router"]
