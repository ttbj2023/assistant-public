"""Graph 同步授权路由单元测试.

Mock 策略: 直接调用路由函数, `_Request` 假对象注入身份,
patch 路由模块的 `get_graph_auth_service` 为 AsyncMock 服务.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.sync import (
    get_msgraph_sync_status,
    revoke_msgraph_authorization,
    start_msgraph_authorization,
)


class _Request:
    def __init__(self, user_id: str = "alice") -> None:
        self.state = SimpleNamespace(user_id=user_id, thread_id="main")


_FLOW = {
    "verification_uri": "https://microsoft.com/devicelogin",
    "user_code": "ABC123",
    "expires_in": 900,
    "interval": 5,
}


class TestAuthorizeRoutes:
    """授权路由测试."""

    @pytest.mark.asyncio
    async def test_authorize_发起成功_返回验证信息(self):
        service = AsyncMock()
        service.start_authorization.return_value = _FLOW

        with patch("src.api.routes.sync.get_graph_auth_service", return_value=service):
            result = await start_msgraph_authorization(_Request())  # type: ignore[arg-type]

        assert result["user_id"] == "alice"
        assert result["authorization"]["user_code"] == "ABC123"
        service.start_authorization.assert_awaited_once_with(
            "alice", thread_id="main",
        )

    @pytest.mark.asyncio
    async def test_authorize_已授权_返回409(self):
        service = AsyncMock()
        service.start_authorization.side_effect = RuntimeError("已授权, 请先解绑")

        with (
            patch("src.api.routes.sync.get_graph_auth_service", return_value=service),
            pytest.raises(HTTPException) as exc_info,
        ):
            await start_msgraph_authorization(_Request())  # type: ignore[arg-type]

        assert exc_info.value.status_code == 409
        assert "已授权" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_status_返回授权状态(self):
        service = AsyncMock()
        # get_status 为同步方法, AsyncMock 会返回 coroutine
        service.get_status = MagicMock(
            return_value={"status": "pending", "user_code": "ABC123"},
        )

        with patch("src.api.routes.sync.get_graph_auth_service", return_value=service):
            result = await get_msgraph_sync_status(_Request())  # type: ignore[arg-type]

        assert result["status"] == "pending"
        service.get_status.assert_called_once_with("alice")

    @pytest.mark.asyncio
    async def test_revoke_返回撤销结果(self):
        service = AsyncMock()
        service.revoke = MagicMock(return_value=True)

        with patch("src.api.routes.sync.get_graph_auth_service", return_value=service):
            result = await revoke_msgraph_authorization(_Request())  # type: ignore[arg-type]

        assert result["revoked"] is True
        service.revoke.assert_called_once_with("alice")

    @pytest.mark.asyncio
    async def test_routes_缺少身份_500(self):
        req = SimpleNamespace(state=SimpleNamespace())

        with pytest.raises(HTTPException) as exc_info:
            await start_msgraph_authorization(req)  # type: ignore[arg-type]

        assert exc_info.value.status_code == 500


class TestSyncSettingsRoutes:
    """per-user 同步设置路由测试."""

    @pytest.mark.asyncio
    async def test_get_settings_返回覆盖值(self):
        from src.api.routes.sync import get_sync_settings

        dao = AsyncMock()
        dao.get_value = AsyncMock(
            side_effect=lambda uid, key: {"timezone": "UTC"}.get(key),
        )
        with patch("src.api.routes.sync._get_setting_dao", AsyncMock(return_value=dao)):
            result = await get_sync_settings(_Request())  # type: ignore[arg-type]

        assert result["user_id"] == "alice"
        assert result["settings"]["timezone"] == "UTC"
        assert result["settings"]["todo_list_name"] is None  # 未覆盖

    @pytest.mark.asyncio
    async def test_put_settings_写入覆盖值(self):
        from src.api.routes.sync import SyncSettingsUpdate, update_sync_settings

        dao = AsyncMock()
        body = SyncSettingsUpdate(todo_list_name="我的助手", timezone="UTC")
        with patch("src.api.routes.sync._get_setting_dao", AsyncMock(return_value=dao)):
            result = await update_sync_settings(_Request(), body)  # type: ignore[arg-type]

        assert result["updated"] is True
        dao.set_value.assert_any_await("alice", "todo_list_name", "我的助手")
        dao.set_value.assert_any_await("alice", "timezone", "UTC")

    @pytest.mark.asyncio
    async def test_put_settings_无效时区_422(self):
        from src.api.routes.sync import SyncSettingsUpdate, update_sync_settings

        body = SyncSettingsUpdate(timezone="Mars/Olympus_Mons")
        with pytest.raises(HTTPException) as exc_info:
            await update_sync_settings(_Request(), body)  # type: ignore[arg-type]

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_put_settings_null值清除覆盖(self):
        from src.api.routes.sync import SyncSettingsUpdate, update_sync_settings

        dao = AsyncMock()
        body = SyncSettingsUpdate(todo_list_name=None)
        with patch("src.api.routes.sync._get_setting_dao", AsyncMock(return_value=dao)):
            result = await update_sync_settings(_Request(), body)  # type: ignore[arg-type]

        assert result["updated"] is True
        dao.delete_value.assert_awaited_once_with("alice", "todo_list_name")
