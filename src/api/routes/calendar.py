"""日历管理 REST API - 日程 CRUD 与订阅管理.

受全局认证中间件保护, 身份取自 request.state (中间件注入).
时间输入 ISO 8601; naive 值视为 UTC (遵循 datetime_utils 约定).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from src.storage.service import create_calendar_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["calendar"])


class CalendarEventCreateRequest(BaseModel):
    """创建日程请求."""

    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    location: str | None = None
    start_time: datetime
    end_time: datetime
    all_day: bool = False
    recurrence_rule: str | None = None
    recurrence_until: datetime | None = None


class CalendarEventUpdateRequest(BaseModel):
    """更新日程请求 (仅显式提供的字段)."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    location: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    all_day: bool | None = None
    recurrence_rule: str | None = None
    recurrence_until: datetime | None = None


@router.get("/v1/calendar/events")
async def list_calendar_events(
    request: Request,
    start_from: datetime | None = Query(default=None),
    start_to: datetime | None = Query(default=None),
    days_ahead: int | None = Query(default=None, ge=1, le=365),
    expand: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """查询日程列表 (时间窗过滤, 默认当前时刻前后各一周).

    expand=true 时把重复规则展开为具体实例 (每个实例含
    is_recurring_instance 标记), 否则返回原始事件定义.
    """
    user_id = _get_user_id(request)
    service = await create_calendar_service(user_id)

    if days_ahead is not None:
        now = datetime.now(UTC)
        window_start, window_end = now, now + timedelta(days=days_ahead)
    elif start_from is not None or start_to is not None:
        window_start = start_from or datetime.now(UTC) - timedelta(days=7)
        window_end = start_to or datetime.now(UTC) + timedelta(days=365)
    else:
        now = datetime.now(UTC)
        window_start, window_end = now - timedelta(days=7), now + timedelta(days=7)

    events = await service.list_events_in_range(window_start, window_end)
    if expand:
        from src.calendar.rrule_utils import expand_events_in_range

        instances = expand_events_in_range(events, window_start, window_end)
        data: list[dict[str, Any]] = [
            {
                **item,
                "start_time": item["start_time"].isoformat(),
                "end_time": item["end_time"].isoformat(),
            }
            for item in instances[:limit]
        ]
    else:
        data = [event.to_dict() for event in events[:limit]]
    return {
        "object": "list",
        "expand": expand,
        "data": data,
    }


@router.post("/v1/calendar/events")
async def create_calendar_event(
    request: Request,
    body: CalendarEventCreateRequest,
) -> dict[str, Any]:
    """创建日程."""
    user_id, thread_id, agent_id = _get_identity(request)
    if body.end_time < body.start_time:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="结束时间不能早于开始时间",
        )

    service = await create_calendar_service(user_id)
    event = await service.create_event(
        title=body.title,
        start_time=_ensure_utc(body.start_time),
        end_time=_ensure_utc(body.end_time),
        description=body.description,
        location=body.location,
        all_day=body.all_day,
        recurrence_rule=body.recurrence_rule,
        recurrence_until=(
            _ensure_utc(body.recurrence_until) if body.recurrence_until else None
        ),
        source_thread_id=thread_id,
        source_agent_id=agent_id,
    )
    return event.to_dict()


@router.get("/v1/calendar/events/{event_id}")
async def get_calendar_event(request: Request, event_id: int) -> dict[str, Any]:
    """查询日程详情."""
    user_id = _get_user_id(request)
    service = await create_calendar_service(user_id)
    event = await service.get_event(event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="日程不存在",
        )
    return event.to_dict()


@router.patch("/v1/calendar/events/{event_id}")
async def update_calendar_event(
    request: Request,
    event_id: int,
    body: CalendarEventUpdateRequest,
) -> dict[str, Any]:
    """更新日程 (仅更新显式提供的字段)."""
    user_id = _get_user_id(request)
    service = await create_calendar_service(user_id)

    update_data: dict[str, Any] = {}
    for field_name in (
        "title",
        "description",
        "location",
        "all_day",
        "recurrence_rule",
    ):
        value = getattr(body, field_name)
        if value is not None:
            update_data[field_name] = value
    for field_name in ("start_time", "end_time", "recurrence_until"):
        value = getattr(body, field_name)
        if value is not None:
            update_data[field_name] = _ensure_utc(value)

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="未提供任何待更新字段",
        )

    try:
        result = await service.update_event(event_id, update_data)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    if result.event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="日程不存在",
        )
    payload = result.event.to_dict()
    if result.cascade.rescheduled_message_ids:
        payload["rescheduled_message_ids"] = result.cascade.rescheduled_message_ids
    return payload


@router.delete("/v1/calendar/events/{event_id}")
async def delete_calendar_event(request: Request, event_id: int) -> dict[str, Any]:
    """删除日程 (物理删除)."""
    user_id = _get_user_id(request)
    service = await create_calendar_service(user_id)
    result = await service.delete_event(event_id)
    if not result.deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="日程不存在",
        )
    return {
        "deleted": True,
        "id": event_id,
        "cancelled_message_ids": result.cascade.cancelled_message_ids,
    }


@router.get("/v1/calendar/subscription")
async def list_calendar_subscriptions(request: Request) -> dict[str, Any]:
    """列出订阅 (不返回 token 明文)."""
    user_id = _get_user_id(request)
    service = await create_calendar_service(user_id)
    subscriptions = await service.list_subscriptions()
    return {
        "object": "list",
        "data": [sub.to_dict() for sub in subscriptions],
    }


@router.post("/v1/calendar/subscription")
async def create_calendar_subscription(request: Request) -> dict[str, Any]:
    """创建订阅 token, 返回完整 ICS 订阅 URL (明文仅本次返回)."""
    user_id = _get_user_id(request)
    service = await create_calendar_service(user_id)
    record, token = await service.create_subscription()

    path = f"/v1/calendar/ics/{user_id}/{token}/calendar.ics"
    base_url = str(request.base_url).rstrip("/")
    return {
        "subscription_id": record.subscription_id,
        "token": token,
        "url": f"{base_url}{path}",
        "path": path,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


@router.delete("/v1/calendar/subscription/{subscription_id}")
async def revoke_calendar_subscription(
    request: Request,
    subscription_id: str,
) -> dict[str, Any]:
    """撤销订阅, 对应 ICS URL 立即失效."""
    user_id = _get_user_id(request)
    service = await create_calendar_service(user_id)
    revoked = await service.revoke_subscription(subscription_id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="订阅不存在",
        )
    return {"revoked": True, "subscription_id": subscription_id}


def _get_user_id(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication middleware error: missing user information",
        )
    return str(user_id)


def _get_identity(request: Request) -> tuple[str, str | None, str | None]:
    user_id = _get_user_id(request)
    thread_id = getattr(request.state, "thread_id", None)
    agent_id = getattr(request.state, "agent_id", None)
    return (
        user_id,
        str(thread_id) if thread_id else None,
        str(agent_id) if agent_id else None,
    )


def _ensure_utc(dt: datetime) -> datetime:
    """naive 输入视为 UTC, 输出统一 aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


__all__ = ["router"]
