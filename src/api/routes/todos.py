"""TODO 管理 REST API - 用户级统一视图的增删改查.

受全局认证中间件保护. 存储为用户级 todo.db (跨线程统一),
thread_id 仅作为行级溯源字段记录.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from src.storage.models.todo import TodoPriority, TodoStatus
from src.storage.service import create_todo_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["todos"])

_PRIORITY_MAP = {p.value: p for p in TodoPriority}
_STATUS_MAP = {s.value: s for s in TodoStatus}


class TodoCreateRequest(BaseModel):
    """创建任务请求."""

    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    priority: str = Field(default="medium")
    status: str = Field(default="pending")
    due_date: datetime | None = None
    tags: str | None = None


class TodoUpdateRequest(BaseModel):
    """更新任务请求 (仅显式提供的字段)."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    priority: str | None = None
    status: str | None = None
    due_date: datetime | None = None
    tags: str | None = None


@router.get("/v1/todos")
async def list_todos(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    priority: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """查询任务列表 (用户级统一视图, 跨线程)."""
    user_id = _get_user_id(request)
    service = await create_todo_service(user_id, "", agent_id="rest-api")

    kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
    if status_filter is not None:
        status_enum = _STATUS_MAP.get(status_filter)
        if status_enum is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"无效状态: {status_filter}",
            )
        kwargs["status"] = status_enum
    if priority is not None:
        priority_enum = _PRIORITY_MAP.get(priority)
        if priority_enum is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"无效优先级: {priority}",
            )
        kwargs["priority"] = priority_enum

    todos = await service.list_todos(user_id, None, **kwargs)
    return {
        "object": "list",
        "data": [todo.to_dict() for todo in todos],
        "limit": limit,
        "offset": offset,
    }


@router.post("/v1/todos")
async def create_todo(request: Request, body: TodoCreateRequest) -> dict[str, Any]:
    """创建任务."""
    user_id, thread_id = _get_identity(request)
    service = await create_todo_service(user_id, thread_id, agent_id="rest-api")

    priority = _PRIORITY_MAP.get(body.priority)
    if priority is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"无效优先级: {body.priority}",
        )
    todo_status = _STATUS_MAP.get(body.status)
    if todo_status is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"无效状态: {body.status}",
        )

    todo = await service.create_todo(
        title=body.title,
        user_id=user_id,
        thread_id=thread_id,
        description=body.description or "",
        priority=priority,
        status=todo_status,
        due_date=_ensure_utc(body.due_date),
    )
    return todo.to_dict()


@router.get("/v1/todos/{todo_id}")
async def get_todo(request: Request, todo_id: int) -> dict[str, Any]:
    """查询任务详情."""
    user_id = _get_user_id(request)
    service = await create_todo_service(user_id, "", agent_id="rest-api")
    todo = await service.get_todo_by_id(todo_id, user_id)
    if todo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在",
        )
    return todo.to_dict()


@router.patch("/v1/todos/{todo_id}")
async def update_todo(
    request: Request,
    todo_id: int,
    body: TodoUpdateRequest,
) -> dict[str, Any]:
    """更新任务 (仅更新显式提供的字段)."""
    user_id = _get_user_id(request)
    service = await create_todo_service(user_id, "", agent_id="rest-api")

    kwargs: dict[str, Any] = {}
    if body.title is not None:
        kwargs["title"] = body.title
    if body.description is not None:
        kwargs["description"] = body.description
    if body.priority is not None:
        priority = _PRIORITY_MAP.get(body.priority)
        if priority is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"无效优先级: {body.priority}",
            )
        kwargs["priority"] = priority
    if body.status is not None:
        todo_status = _STATUS_MAP.get(body.status)
        if todo_status is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"无效状态: {body.status}",
            )
        kwargs["status"] = todo_status
    if body.due_date is not None:
        kwargs["due_date"] = _ensure_utc(body.due_date)

    if not kwargs:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="未提供任何待更新字段",
        )

    todo = await service.update_todo(todo_id, user_id, **kwargs)
    if todo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在",
        )
    return todo.to_dict()


@router.delete("/v1/todos/{todo_id}")
async def delete_todo(request: Request, todo_id: int) -> dict[str, Any]:
    """删除任务 (物理删除)."""
    user_id = _get_user_id(request)
    service = await create_todo_service(user_id, "", agent_id="rest-api")
    deleted = await service.delete_todo(todo_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在",
        )
    return {"deleted": True, "id": todo_id}


def _get_user_id(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication middleware error: missing user information",
        )
    return str(user_id)


def _get_identity(request: Request) -> tuple[str, str]:
    user_id = _get_user_id(request)
    thread_id = getattr(request.state, "thread_id", None)
    return user_id, str(thread_id) if thread_id else ""


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """naive 输入视为 UTC, 输出统一 aware UTC."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


__all__ = ["router"]
