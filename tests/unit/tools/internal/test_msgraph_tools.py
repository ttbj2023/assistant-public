"""MSGraph 连接/状态工具测试.

Mock 策略: patch 工具模块的 get_graph_auth_service / get_graph_sync_engine,
验证工具对授权状态机的分支行为与输出结构.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.shared.tool_runtime import inject_identity


def _parse(result: str) -> dict:
    return json.loads(result)


class TestMsGraphConnect:
    """msgraph_connect 工具测试."""

    @pytest.mark.asyncio
    async def test_已授权_工具不可见(self):
        """认证一次性: token 已存在时 is_available=False, 从休眠池与搜索 catalog 隐藏."""
        from src.tools.internal.msgraph_connect_tool import MsGraphConnectTool

        tool = MsGraphConnectTool()
        inject_identity(tool, "u1", "t1", "a1")

        with patch(
            "src.sync.token_store.GraphTokenStore.exists",
            return_value=True,
        ):
            assert await tool.is_available() is False

    @pytest.mark.asyncio
    async def test_未授权_工具可见(self):
        from src.tools.internal.msgraph_connect_tool import MsGraphConnectTool

        tool = MsGraphConnectTool()
        inject_identity(tool, "u1", "t1", "a1")

        with patch(
            "src.sync.token_store.GraphTokenStore.exists",
            return_value=False,
        ):
            assert await tool.is_available() is True

    @pytest.mark.asyncio
    async def test_未授权_发起并返回验证信息(self):
        from src.tools.internal.msgraph_connect_tool import MsGraphConnectTool

        service = AsyncMock()
        service.get_status = MagicMock(return_value={"status": "none"})
        service.start_authorization = AsyncMock(
            return_value={
                "verification_uri": "https://microsoft.com/devicelogin",
                "user_code": "ABC123",
                "expires_in": 900,
                "interval": 5,
            },
        )
        tool = MsGraphConnectTool()
        inject_identity(tool, "u1", "t1", "a1")

        with patch(
            "src.tools.internal.msgraph_connect_tool.get_graph_auth_service",
            return_value=service,
        ):
            result = _parse(await tool._arun())

        assert result["success"] is True
        assert result["verification_uri"] == "https://microsoft.com/devicelogin"
        assert result["user_code"] == "ABC123"
        # 透传对话上下文, 供授权完成后向该对话渠道回执
        service.start_authorization.assert_awaited_once_with(
            "u1", thread_id="t1", agent_id="a1",
        )

    @pytest.mark.asyncio
    async def test_已授权_不重复发起(self):
        from src.tools.internal.msgraph_connect_tool import MsGraphConnectTool

        service = AsyncMock()
        service.get_status = MagicMock(return_value={"status": "authorized"})
        service.start_authorization = AsyncMock()
        tool = MsGraphConnectTool()
        inject_identity(tool, "u1", "t1", "a1")

        with patch(
            "src.tools.internal.msgraph_connect_tool.get_graph_auth_service",
            return_value=service,
        ):
            result = _parse(await tool._arun())

        assert result["success"] is True
        assert "已授权" in result["message"]
        service.start_authorization.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_进行中_返回待验证信息(self):
        from src.tools.internal.msgraph_connect_tool import MsGraphConnectTool

        service = AsyncMock()
        service.get_status = MagicMock(
            return_value={
                "status": "pending",
                "verification_uri": "https://microsoft.com/devicelogin",
                "user_code": "XYZ789",
            },
        )
        tool = MsGraphConnectTool()
        inject_identity(tool, "u1", "t1", "a1")

        with patch(
            "src.tools.internal.msgraph_connect_tool.get_graph_auth_service",
            return_value=service,
        ):
            result = _parse(await tool._arun())

        assert result["success"] is True
        assert result["user_code"] == "XYZ789"

    @pytest.mark.asyncio
    async def test_发起异常_返回失败(self):
        from src.tools.internal.msgraph_connect_tool import MsGraphConnectTool

        service = AsyncMock()
        service.get_status = MagicMock(return_value={"status": "failed", "error": "x"})
        service.start_authorization = AsyncMock(
            side_effect=RuntimeError("授权服务不可用"),
        )
        tool = MsGraphConnectTool()
        inject_identity(tool, "u1", "t1", "a1")

        with patch(
            "src.tools.internal.msgraph_connect_tool.get_graph_auth_service",
            return_value=service,
        ):
            result = _parse(await tool._arun())

        assert result["success"] is False


class TestMsGraphSyncStatus:
    """msgraph_sync_status 工具测试."""

    @pytest.mark.asyncio
    async def test_返回授权状态与引擎结果(self):
        from src.tools.internal.msgraph_sync_status_tool import (
            MsGraphSyncStatusTool,
        )

        service = AsyncMock()
        service.get_status = MagicMock(return_value={"status": "authorized"})
        engine = MagicMock()
        engine.stats.running = True
        engine.stats.user_outcomes = {"u1": "ok"}
        tool = MsGraphSyncStatusTool()
        inject_identity(tool, "u1", "t1", "a1")

        with (
            patch(
                "src.tools.internal.msgraph_sync_status_tool.get_graph_auth_service",
                return_value=service,
            ),
            patch(
                "src.tools.internal.msgraph_sync_status_tool.get_graph_sync_engine",
                return_value=engine,
            ),
        ):
            result = _parse(await tool._arun())

        assert result["success"] is True
        assert result["auth_status"] == "authorized"
        assert result["engine_running"] is True
        assert result["last_sync_outcome"] == "ok"
