"""定时消息全生命周期集成测试.

验证定时消息从创建→调度注册→触发发送→状态流转→取消的端到端闭环,
补充单元测试中 Mock OpenClawClient + session_factory 只测错误分支的缺口.

测试策略: 灰盒 - 真实 ScheduledMessageService + AsyncScheduledMessageDAO + SQLite,
仅 Mock 外部依赖 (OpenClaw HTTP 客户端).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.storage.dao import async_database_manager as adm
from src.storage.models.scheduled_message import MessageStatus
from src.storage.service.scheduled_message_service import (
    ScheduledMessageService,
    _ServiceRegistry,
)
from src.storage.service.service_factory import (
    clear_vector_cache,
)

_AGENT_ID = "test-agent"


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    """重置 DB 全局状态 + Service 缓存 + ServiceRegistry, 避免跨事件循环污染."""
    adm._db_cache_lock = asyncio.Lock()
    adm._db_manager_cache.clear()
    clear_vector_cache()
    _ServiceRegistry.clear()
    yield
    adm._db_cache_lock = asyncio.Lock()
    adm._db_manager_cache.clear()
    clear_vector_cache()
    _ServiceRegistry.clear()


async def _create_service(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str = _AGENT_ID,
) -> ScheduledMessageService:
    """创建真实 ScheduledMessageService 实例 (带缓存)."""
    from src.storage.dao.async_database_manager import (
        create_async_scheduled_message_db_manager,
    )

    db_manager = await create_async_scheduled_message_db_manager(
        user_id, thread_id, agent_id=agent_id
    )
    service = ScheduledMessageService(
        session_factory=db_manager.session_factory,
        user_id=user_id,
        thread_id=thread_id,
        agent_id=agent_id,
    )
    _ServiceRegistry[f"{user_id}:{thread_id}:{agent_id}"] = service
    return service


@pytest.mark.integration
class TestScheduledMessageLifecycleIntegration:
    """定时消息全生命周期集成测试."""

    @pytest.mark.asyncio
    async def test_schedule_message_creates_pending_record_and_timer(
        self,
        test_user,
        test_thread_id,
    ):
        """创建定时消息 → DAO 写入 PENDING 记录 → 内存定时器注册.

        Mock 边界: 无 (全真实)
        验证重点: 返回的 msg 含 message_id; _timers 中有该 message_id; status=PENDING
        """
        service = await _create_service(test_user, test_thread_id)
        future_time = datetime.now(UTC) + timedelta(hours=1)

        msg = await service.schedule_message(
            "提醒喝水",
            future_time,
        )

        assert msg is not None
        assert msg.message_id.startswith("msg_")
        assert msg.status == MessageStatus.PENDING
        assert msg.message == "提醒喝水"
        assert msg.message_id in service._timers

    @pytest.mark.asyncio
    async def test_send_wechat_success_marks_sent(
        self,
        test_user,
        test_thread_id,
    ):
        """PENDING 消息 → Mock OpenClaw 发送成功 → status=SENT 且 sent_at 非空.

        Mock 边界: get_openclaw_client 返回 send_message=True; 预填渠道配置缓存
        验证重点: _send_message 后 status 变为 SENT; sent_at 被写入
        """
        service = await _create_service(test_user, test_thread_id)
        future_time = datetime.now(UTC) + timedelta(hours=1)

        msg = await service.schedule_message(
            "微信消息内容",
            future_time,
            channel="wechat",
        )

        from src.core.notification import DeliverySpec

        delivery = DeliverySpec(
            method="wechat",
            openclaw_channel="openclaw-weixin",
            account_id="bot-1",
            target="user-target",
        )
        mock_notifier = MagicMock()
        mock_notifier.send = AsyncMock(return_value=True)

        with (
            patch(
                "src.core.notification.resolve_delivery",
                new=AsyncMock(return_value=delivery),
            ),
            patch(
                "src.core.notification.get_notification_service",
                return_value=mock_notifier,
            ),
        ):
            await service._send_message(msg.message_id)

        refreshed = await service.dao.get_by_message_id(msg.message_id)
        assert refreshed is not None
        assert refreshed.status == MessageStatus.SENT
        assert refreshed.sent_at is not None
        mock_notifier.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_wechat_failure_marks_failed(
        self,
        test_user,
        test_thread_id,
    ):
        """PENDING 消息 → Mock OpenClaw 发送失败 → status=FAILED.

        Mock 边界: get_openclaw_client 返回 send_message=False; 预填渠道配置缓存
        验证重点: _send_message 后 status 变为 FAILED
        """
        service = await _create_service(test_user, test_thread_id)
        future_time = datetime.now(UTC) + timedelta(hours=1)

        msg = await service.schedule_message(
            "发送失败的消息",
            future_time,
            channel="wechat",
        )

        from src.core.notification import DeliverySpec

        delivery = DeliverySpec(
            method="wechat",
            openclaw_channel="openclaw-weixin",
            account_id="bot-1",
            target="user-target",
        )
        mock_notifier = MagicMock()
        mock_notifier.send = AsyncMock(return_value=False)

        with (
            patch(
                "src.core.notification.resolve_delivery",
                new=AsyncMock(return_value=delivery),
            ),
            patch(
                "src.core.notification.get_notification_service",
                return_value=mock_notifier,
            ),
        ):
            await service._send_message(msg.message_id)

        refreshed = await service.dao.get_by_message_id(msg.message_id)
        assert refreshed is not None
        assert refreshed.status == MessageStatus.FAILED

    @pytest.mark.asyncio
    async def test_cancel_message_removes_timer_and_updates_status(
        self,
        test_user,
        test_thread_id,
    ):
        """取消 PENDING 消息 → 定时器移除 → status=CANCELLED. 再次取消抛 ValueError.

        Mock 边界: 无 (全真实, _cancel_timer 操作真实 asyncio.TimerHandle)
        验证重点: cancel 后 _timers 无该 ID; DAO status=CANCELLED; 二次 cancel 抛 ValueError
        """
        service = await _create_service(test_user, test_thread_id)
        future_time = datetime.now(UTC) + timedelta(hours=1)

        msg = await service.schedule_message(
            "待取消的消息",
            future_time,
        )
        assert msg.message_id in service._timers

        success = await service.cancel_message(msg.message_id)
        assert success is True
        assert msg.message_id not in service._timers

        refreshed = await service.dao.get_by_message_id(msg.message_id)
        assert refreshed is not None
        assert refreshed.status == MessageStatus.CANCELLED

        with pytest.raises(ValueError, match="cancelled"):
            await service.cancel_message(msg.message_id)

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_raises_error(
        self,
        test_user,
        test_thread_id,
    ):
        """取消不存在的消息抛 FileNotFoundError."""
        service = await _create_service(test_user, test_thread_id)

        with pytest.raises(FileNotFoundError):
            await service.cancel_message("nonexistent-id")

    @pytest.mark.asyncio
    async def test_send_nonexistent_message_skipped(
        self,
        test_user,
        test_thread_id,
    ):
        """发送不存在的消息 ID 被安全跳过, 不抛异常."""
        service = await _create_service(test_user, test_thread_id)

        # 直接调用 _send_message, 不存在时应跳过
        await service._send_message("nonexistent-id")

        # 无需额外断言, 不抛异常即为通过
