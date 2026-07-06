"""scheduled_message_service 单元测试.

覆盖 schedule_message / cancel / missed / 发送派发(经 NotificationService) /
initialize / shutdown / shutdown_all. 渠道解析与派发已下沉到 NotificationService,
本测试 mock resolve_delivery + get_notification_service 验证编排.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.notification import DeliverySpec
from src.storage.models.scheduled_message import MessageStatus, ScheduledMessage
from src.storage.service.scheduled_message_service import ScheduledMessageService


@pytest.fixture
def mock_msg():
    """构造一条待发送的 ScheduledMessage."""
    return ScheduledMessage(
        id=1,
        message_id="msg-test-1",
        message="测试消息内容",
        send_time=datetime.now(UTC),
        status=MessageStatus.PENDING,
        channel="wechat",
        user_id="user-1",
        thread_id="thread-1",
        agent_id="personal-assistant",
    )


@pytest.fixture
def service():
    """构造一个不初始化 DAO 的 ScheduledMessageService."""
    return ScheduledMessageService(
        session_factory=MagicMock(),
        user_id="user-1",
        thread_id="thread-1",
        agent_id="personal-assistant",
    )


def _wechat_delivery() -> DeliverySpec:
    return DeliverySpec(
        method="wechat",
        openclaw_channel="openclaw-weixin",
        account_id="bot-1",
        target="user-123",
    )


class TestScheduleMessage:
    @pytest.mark.asyncio
    async def test_should_auto_bump_past_time_to_asap(self, service):
        """过去的时间应自动顺延 30 秒视为尽快发送."""
        past_time = datetime.now(UTC) - timedelta(hours=1)
        service.dao.count_pending = AsyncMock(return_value=0)
        created_send_time = None

        async def mock_create(**kwargs):
            nonlocal created_send_time
            created_send_time = kwargs["send_time"]
            return ScheduledMessage(
                id=1,
                message_id="msg-1",
                message=kwargs["message"],
                send_time=created_send_time,
                status=MessageStatus.PENDING,
                channel=kwargs.get("channel", "wechat"),
                user_id=kwargs["user_id"],
                thread_id=kwargs["thread_id"],
                agent_id=kwargs["agent_id"],
            )

        service.dao.create_message = AsyncMock(side_effect=mock_create)

        result = await service.schedule_message("测试消息", past_time)

        assert result.message_id == "msg-1"
        assert created_send_time is not None
        now_utc = datetime.now(UTC).replace(tzinfo=None)
        assert now_utc <= created_send_time <= now_utc + timedelta(seconds=60)

    @pytest.mark.asyncio
    async def test_should_auto_bump_current_time_to_asap(self, service):
        """当前时间应自动顺延 30 秒视为尽快发送."""
        now_time = datetime.now(UTC)
        service.dao.count_pending = AsyncMock(return_value=0)
        created_send_time = None

        async def mock_create(**kwargs):
            nonlocal created_send_time
            created_send_time = kwargs["send_time"]
            return ScheduledMessage(
                id=1,
                message_id="msg-1",
                message=kwargs["message"],
                send_time=created_send_time,
                status=MessageStatus.PENDING,
                channel=kwargs.get("channel", "wechat"),
                user_id=kwargs["user_id"],
                thread_id=kwargs["thread_id"],
                agent_id=kwargs["agent_id"],
            )

        service.dao.create_message = AsyncMock(side_effect=mock_create)

        await service.schedule_message("测试消息", now_time)

        assert created_send_time is not None
        now_utc = datetime.now(UTC).replace(tzinfo=None)
        assert now_utc <= created_send_time <= now_utc + timedelta(seconds=60)

    @pytest.mark.asyncio
    async def test_should_reject_too_far_ahead_time(self, service):
        """超出最大提前时间应被拒绝."""
        future_time = datetime.now(UTC) + timedelta(hours=200)

        with pytest.raises(ValueError, match="不能超过"):
            await service.schedule_message("测试消息", future_time)


class TestCancelMessage:
    @pytest.mark.asyncio
    async def test_should_raise_when_message_not_found(self, service):
        """消息不存在时应抛出FileNotFoundError."""
        service.dao.get_by_message_id = AsyncMock(return_value=None)

        with pytest.raises(FileNotFoundError, match="消息不存在"):
            await service.cancel_message("nonexistent-id")

    @pytest.mark.asyncio
    async def test_should_raise_when_status_not_pending(self, service, mock_msg):
        """非pending状态的消息应抛出ValueError."""
        mock_msg.status = MessageStatus.SENT
        service.dao.get_by_message_id = AsyncMock(return_value=mock_msg)

        with pytest.raises(ValueError, match="只有pending状态"):
            await service.cancel_message(mock_msg.message_id)


class TestGetAndAcknowledgeMissedMessages:
    @pytest.mark.asyncio
    async def test_should_return_empty_when_no_missed(self, service):
        """无missed消息时应返回空字符串."""
        service.dao.get_missed_messages = AsyncMock(return_value=[])

        result = await service.get_and_acknowledge_missed_messages()

        assert result == ""

    @pytest.mark.asyncio
    async def test_should_format_missed_messages(self, service, mock_msg):
        """应格式化missed消息并标记为notified."""
        mock_msg.description = "测试提醒"
        mock_msg.status = MessageStatus.MISSED
        service.dao.get_missed_messages = AsyncMock(return_value=[mock_msg])
        service.dao.update_status = AsyncMock()

        result = await service.get_and_acknowledge_missed_messages()

        assert mock_msg.message in result
        assert "测试提醒" in result
        service.dao.update_status.assert_called_once_with(
            mock_msg.message_id,
            MessageStatus.NOTIFIED,
        )

    @pytest.mark.asyncio
    async def test_should_return_empty_on_exception(self, service):
        """异常时应返回空字符串而不是抛出."""
        service.dao.get_missed_messages = AsyncMock(side_effect=Exception("DB error"))

        result = await service.get_and_acknowledge_missed_messages()

        assert result == ""


class TestListPendingMessages:
    @pytest.mark.asyncio
    async def test_should_delegate_to_dao(self, service):
        """应委托给DAO查询."""
        service.dao.get_pending_messages = AsyncMock(return_value=[])

        result = await service.list_pending_messages()

        assert result == []
        service.dao.get_pending_messages.assert_called_once()


class TestSendMessageDispatch:
    """测试_send_message消息分发逻辑 (经 NotificationService 派发)."""

    @pytest.mark.asyncio
    async def test_send_message_should_skip_when_not_pending(self, service, mock_msg):
        """消息状态非pending时应跳过发送."""
        mock_msg.status = MessageStatus.SENT
        service.dao.get_by_message_id = AsyncMock(return_value=mock_msg)
        service.dao.update_status = AsyncMock()

        await service._send_message(mock_msg.message_id)

        service.dao.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_should_skip_when_not_found(self, service):
        """消息不存在时应跳过发送."""
        service.dao.get_by_message_id = AsyncMock(return_value=None)
        service.dao.update_status = AsyncMock()

        await service._send_message("nonexistent-id")

        service.dao.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_should_mark_failed_when_no_delivery(
        self, service, mock_msg
    ):
        """resolve_delivery 返回 None (渠道缺失/无效) 应标记 FAILED."""
        service.dao.get_by_message_id = AsyncMock(return_value=mock_msg)
        service.dao.update_status = AsyncMock()

        with patch(
            "src.core.notification.resolve_delivery", new=AsyncMock(return_value=None)
        ):
            await service._send_message(mock_msg.message_id)

        service.dao.update_status.assert_called_once_with(
            mock_msg.message_id, MessageStatus.FAILED
        )

    @pytest.mark.asyncio
    async def test_send_message_should_mark_sent_on_success(self, service, mock_msg):
        """发送成功应标记为SENT."""
        service.dao.get_by_message_id = AsyncMock(return_value=mock_msg)
        service.dao.update_status = AsyncMock()
        mock_notifier = MagicMock()
        mock_notifier.send = AsyncMock(return_value=True)

        with (
            patch(
                "src.core.notification.resolve_delivery",
                new=AsyncMock(return_value=_wechat_delivery()),
            ),
            patch(
                "src.core.notification.get_notification_service",
                return_value=mock_notifier,
            ),
        ):
            await service._send_message(mock_msg.message_id)

        call_args = service.dao.update_status.call_args
        assert call_args[0][1] == MessageStatus.SENT
        mock_notifier.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_message_should_mark_failed_on_failure(self, service, mock_msg):
        """NotificationService.send 返回 False 应标记 FAILED."""
        service.dao.get_by_message_id = AsyncMock(return_value=mock_msg)
        service.dao.update_status = AsyncMock()
        mock_notifier = MagicMock()
        mock_notifier.send = AsyncMock(return_value=False)

        with (
            patch(
                "src.core.notification.resolve_delivery",
                new=AsyncMock(return_value=_wechat_delivery()),
            ),
            patch(
                "src.core.notification.get_notification_service",
                return_value=mock_notifier,
            ),
        ):
            await service._send_message(mock_msg.message_id)

        call_args = service.dao.update_status.call_args
        assert call_args[0][1] == MessageStatus.FAILED


class TestScheduleMessageEdgeCases:
    """测试schedule_message的边缘情况."""

    @pytest.mark.asyncio
    async def test_should_accept_naive_datetime_and_convert(self, service):
        """无时区的datetime应按timezone参数转换."""
        now_local = datetime.now()
        naive_time = now_local.replace(tzinfo=None) + timedelta(hours=100)
        service.dao.count_pending = AsyncMock(return_value=0)
        service.dao.create_message = AsyncMock(
            return_value=ScheduledMessage(
                id=1,
                message_id="msg-1",
                message="测试",
                send_time=naive_time,
                status=MessageStatus.PENDING,
                channel="wechat",
                user_id="user-1",
                thread_id="thread-1",
                agent_id="personal-assistant",
            )
        )

        result = await service.schedule_message("测试消息", naive_time)

        assert result.message_id == "msg-1"

    @pytest.mark.asyncio
    async def test_should_reject_when_max_pending_reached(self, service):
        """待发送消息达上限时应拒绝."""
        future_time = datetime.now(UTC) + timedelta(hours=5)
        service.dao.count_pending = AsyncMock(return_value=50)

        with pytest.raises(ValueError, match="已达上限"):
            await service.schedule_message("测试消息", future_time)

    @pytest.mark.asyncio
    async def test_should_use_default_channel_when_none(self, service):
        """未指定渠道时应使用默认渠道."""
        future_time = datetime.now(UTC) + timedelta(hours=5)
        service.dao.count_pending = AsyncMock(return_value=0)
        service._default_channel = "email"
        created_msg = None

        async def mock_create(**kwargs):
            nonlocal created_msg
            created_msg = ScheduledMessage(
                id=1,
                message_id="msg-1",
                message="测试",
                send_time=future_time.replace(tzinfo=None),
                status=MessageStatus.PENDING,
                channel=kwargs.get("channel", "wechat"),
                user_id="user-1",
                thread_id="thread-1",
                agent_id="personal-assistant",
            )
            return created_msg

        service.dao.create_message = AsyncMock(side_effect=mock_create)

        await service.schedule_message("测试消息", future_time, channel=None)

        assert created_msg.channel == "email"


class TestHealthCheck:
    """测试定时消息服务健康检查."""

    @pytest.mark.asyncio
    async def test_health_check_should_return_healthy(self, service):
        """健康检查: 正常时应返回healthy."""
        service.dao.health_check = AsyncMock(return_value=True)

        result = await service.health_check()

        assert result["status"] == "healthy"
        assert result["database_connected"] is True

    @pytest.mark.asyncio
    async def test_health_check_should_return_unhealthy(self, service):
        """健康检查: 异常时应返回unhealthy."""
        service.dao.health_check = AsyncMock(side_effect=Exception("DB error"))

        result = await service.health_check()

        assert result["status"] == "unhealthy"
        assert result["database_connected"] is False


class TestInitialize:
    """测试initialize方法."""

    @pytest.mark.asyncio
    async def test_initialize_should_mark_expired(self, service):
        """初始化时应标记过期消息."""
        service.dao.mark_expired_as_missed = AsyncMock(return_value=3)
        service.dao.get_pending_messages = AsyncMock(return_value=[])

        await service.initialize()

        service.dao.mark_expired_as_missed.assert_called_once()
        assert service._initialized is True

    @pytest.mark.asyncio
    async def test_initialize_should_not_run_twice(self, service):
        """初始化不应重复执行."""
        mock_mark = AsyncMock(return_value=0)
        service.dao.mark_expired_as_missed = mock_mark
        service._initialized = True

        await service.initialize()

        mock_mark.assert_not_called()

    @pytest.mark.asyncio
    async def test_initialize_should_register_pending_timers(self, service, mock_msg):
        """初始化时应注册pending消息的定时器."""
        service.dao.mark_expired_as_missed = AsyncMock(return_value=0)
        service.dao.get_pending_messages = AsyncMock(return_value=[mock_msg])

        await service.initialize()

        assert service._initialized is True


class TestShutdown:
    """测试ScheduledMessageService.shutdown优雅关闭."""

    @pytest.mark.asyncio
    async def test_shutdown_cancels_all_timers(self, service):
        """shutdown应cancel所有未触发的定时器并清空_timers."""
        loop = asyncio.get_event_loop()
        handle1 = loop.call_later(100, lambda: None)
        handle2 = loop.call_later(200, lambda: None)
        service._timers["msg-1"] = handle1
        service._timers["msg-2"] = handle2

        await service.shutdown()

        assert len(service._timers) == 0
        assert handle1.cancelled()
        assert handle2.cancelled()

    @pytest.mark.asyncio
    async def test_shutdown_awaits_inflight_tasks(self, service):
        """shutdown应在超时内等待在途发送任务完成."""
        completed = False

        async def quick_task():
            nonlocal completed
            await asyncio.sleep(0.01)
            completed = True

        task = asyncio.ensure_future(quick_task())
        service._background_tasks.add(task)

        await service.shutdown(task_timeout=1.0)

        assert completed is True
        assert len(service._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_shutdown_cancels_tasks_on_timeout(self, service):
        """在途任务超过超时阈值时应被cancel."""

        async def hanging_task():
            await asyncio.sleep(100)

        task = asyncio.ensure_future(hanging_task())
        service._background_tasks.add(task)

        await service.shutdown(task_timeout=0.05)

        assert len(service._background_tasks) == 0
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_shutdown_handles_empty_state(self, service):
        """空状态shutdown不应报错."""
        await service.shutdown()

        assert len(service._timers) == 0
        assert len(service._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_shutdown_skips_already_done_tasks(self, service):
        """已完成的任务不应阻塞shutdown."""
        done_task = asyncio.ensure_future(asyncio.sleep(0))
        await done_task
        service._background_tasks.add(done_task)

        await service.shutdown(task_timeout=1.0)

        assert len(service._background_tasks) == 0


class TestShutdownAllServices:
    """测试shutdown_all_scheduled_services模块级清理."""

    def teardown_method(self):
        """每个测试后清理模块级注册表, 保证隔离."""
        from src.storage.service import scheduled_message_service as mod

        mod._ServiceRegistry.clear()

    @pytest.mark.asyncio
    async def test_shutdown_all_clears_registry(self):
        """应逐实例shutdown并清空注册表."""
        from src.storage.service import scheduled_message_service as mod

        svc1 = MagicMock()
        svc1.shutdown = AsyncMock()
        svc2 = MagicMock()
        svc2.shutdown = AsyncMock()
        mod._ServiceRegistry["u1:t1:a1"] = svc1
        mod._ServiceRegistry["u2:t2:a2"] = svc2

        await mod.shutdown_all_scheduled_services()

        assert len(mod._ServiceRegistry) == 0
        svc1.shutdown.assert_awaited_once()
        svc2.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_all_tolerates_individual_failures(self):
        """单个实例shutdown异常不应中断整体清理."""
        from src.storage.service import scheduled_message_service as mod

        bad_svc = MagicMock()
        bad_svc.shutdown = AsyncMock(side_effect=Exception("boom"))
        good_svc = MagicMock()
        good_svc.shutdown = AsyncMock()
        mod._ServiceRegistry["bad"] = bad_svc
        mod._ServiceRegistry["good"] = good_svc

        await mod.shutdown_all_scheduled_services()

        assert len(mod._ServiceRegistry) == 0
        good_svc.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_all_handles_empty_registry(self):
        """空注册表不应报错."""
        from src.storage.service import scheduled_message_service as mod

        mod._ServiceRegistry.clear()

        await mod.shutdown_all_scheduled_services()

        assert len(mod._ServiceRegistry) == 0
