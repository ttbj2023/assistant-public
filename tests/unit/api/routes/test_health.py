"""健康检查路由单元测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

import src.api.routes.health as health_module


def _healthy_storage_result() -> dict:
    return {"status": "healthy", "message": "ok"}


def _healthy_agent_result() -> dict:
    return {"overall_status": "healthy", "overall_message": "ok"}


@pytest.mark.asyncio
async def test_uptime_seconds_reflects_process_start_time() -> None:
    """uptime_seconds 应记录进程启动至今的秒数, 而非当前时间戳."""
    with (
        patch.object(health_module, "_PROCESS_START_TIME", 1_000_000.0),
        patch.object(health_module.time, "time", return_value=1_000_100.0),
        patch.object(
            health_module,
            "_check_storage_health",
            AsyncMock(return_value=_healthy_storage_result()),
        ),
        patch.object(
            health_module,
            "_check_agent_health",
            Mock(return_value=_healthy_agent_result()),
        ),
    ):
        resp = await health_module.health_check()

    assert resp.uptime_seconds == 100
    assert resp.uptime_seconds != resp.timestamp


@pytest.mark.asyncio
async def test_uptime_seconds_is_not_epoch_timestamp() -> None:
    """正常运行时应报告较小的运行时长, 而非当前时间戳量级."""
    with (
        patch.object(
            health_module,
            "_check_storage_health",
            AsyncMock(return_value=_healthy_storage_result()),
        ),
        patch.object(
            health_module,
            "_check_agent_health",
            Mock(return_value=_healthy_agent_result()),
        ),
    ):
        resp = await health_module.health_check()

    assert 0 <= resp.uptime_seconds < 3600
    assert resp.uptime_seconds != resp.timestamp
