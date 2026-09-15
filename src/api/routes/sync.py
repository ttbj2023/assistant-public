"""Microsoft Graph 同步授权 REST API - device flow 发起/状态/解绑 + per-user 设置.

受全局认证中间件保护, 身份取自 request.state (中间件注入).
状态语义: none(未授权) / pending(等待用户输码) / failed(授权失败) /
authorized(token 文件存在).
"""

from __future__ import annotations

import json
import logging
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.sync.graph_auth_service import get_graph_auth_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sync"])

# 可被 per-user 覆盖的设置键 (null = 清除覆盖, 回退全局默认)
_SETTING_KEYS = ("todo_list_name", "calendar_name", "timezone", "notify_delivery")


def _get_user_id(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication middleware error: missing user information",
        )
    return str(user_id)


async def _get_setting_dao(user_id: str) -> Any:
    """构造该用户的同步设置 DAO (graph_sync.db)."""
    from src.storage.dao.async_database_manager import (
        create_async_graph_sync_db_manager,
    )
    from src.storage.dao.async_sync_setting_dao import AsyncSyncSettingDAO

    db = await create_async_graph_sync_db_manager(user_id)
    return AsyncSyncSettingDAO(db.session_factory)


@router.post("/v1/sync/msgraph/authorize")
async def start_msgraph_authorization(request: Request) -> dict[str, Any]:
    """发起 device code flow, 返回验证地址与用户码 (后台轮询授权结果)."""
    user_id = _get_user_id(request)
    thread_id = str(getattr(request.state, "thread_id", "") or "")
    try:
        flow = await get_graph_auth_service().start_authorization(
            user_id,
            thread_id=thread_id,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"user_id": user_id, "authorization": flow}


@router.get("/v1/sync/msgraph/status")
async def get_msgraph_sync_status(request: Request) -> dict[str, Any]:
    """查询授权状态 (pending 附验证信息, failed 附原因)."""
    user_id = _get_user_id(request)
    return {"user_id": user_id, **get_graph_auth_service().get_status(user_id)}


@router.delete("/v1/sync/msgraph/authorize")
async def revoke_msgraph_authorization(request: Request) -> dict[str, Any]:
    """解绑: 取消进行中的授权并删除 token 文件."""
    user_id = _get_user_id(request)
    revoked = get_graph_auth_service().revoke(user_id)
    return {"user_id": user_id, "revoked": revoked}


class SyncSettingsUpdate(BaseModel):
    """per-user 同步设置更新体 (字段缺省 = 不动, null = 清除覆盖)."""

    todo_list_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="远端专用清单名",
    )
    calendar_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="远端专用子日历名",
    )
    timezone: str | None = Field(
        default=None,
        max_length=64,
        description="IANA 时区 (请求体时间换算)",
    )
    notify_delivery: dict[str, Any] | None = Field(
        default=None,
        description="授权失效通知渠道 (DeliverySpec dict)",
    )


@router.get("/v1/sync/msgraph/settings")
async def get_sync_settings(request: Request) -> dict[str, Any]:
    """查询 per-user 覆盖 (未覆盖的键为 null, 生效值回退全局默认)."""
    user_id = _get_user_id(request)
    dao = await _get_setting_dao(user_id)
    settings: dict[str, Any] = {}
    for key in _SETTING_KEYS:
        raw = await dao.get_value(user_id, key)
        if key == "notify_delivery" and raw is not None:
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = None
        settings[key] = raw
    return {"user_id": user_id, "settings": settings}


@router.put("/v1/sync/msgraph/settings")
async def update_sync_settings(
    request: Request,
    body: SyncSettingsUpdate,
) -> dict[str, Any]:
    """更新 per-user 覆盖 (字段缺省不动, 显式 null 清除回退全局默认)."""
    user_id = _get_user_id(request)
    if body.timezone is not None:
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise HTTPException(
                status_code=422,
                detail=f"无效时区: {body.timezone}",
            ) from e
    dao = await _get_setting_dao(user_id)
    provided = body.model_fields_set
    for key in _SETTING_KEYS:
        if key not in provided:
            continue
        value = getattr(body, key)
        if value is None:
            await dao.delete_value(user_id, key)
        elif key == "notify_delivery":
            await dao.set_value(user_id, key, json.dumps(value, ensure_ascii=False))
        else:
            await dao.set_value(user_id, key, str(value))
    return {"user_id": user_id, "updated": True}
