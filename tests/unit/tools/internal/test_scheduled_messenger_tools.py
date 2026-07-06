"""定时消息子工具单元测试.

覆盖拆分后的三个子工具:
- ScheduleMessageTool (schedule_message)
- ListScheduledMessagesTool (list_scheduled_messages)
- CancelScheduledMessageTool (cancel_scheduled_message)

以及共享基类 ScheduledMessengerBase 的公共逻辑.
Mock外部依赖: get_scheduled_message_service, get_user_channel_config_service, get_auth_manager.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.internal.cancel_scheduled_message_tool import CancelScheduledMessageTool
from src.tools.internal.list_scheduled_messages_tool import ListScheduledMessagesTool
from src.tools.internal.schedule_message_tool import ScheduleMessageTool


@pytest.fixture
def schedule_tool():
    return ScheduleMessageTool(user_id="u1", thread_id="t1", agent_id="a1")


@pytest.fixture
def list_tool():
    return ListScheduledMessagesTool(user_id="u1", thread_id="t1", agent_id="a1")


@pytest.fixture
def cancel_tool():
    return CancelScheduledMessageTool(user_id="u1", thread_id="t1", agent_id="a1")


@pytest.fixture
def mock_msg_service():
    svc = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = "msg-001"
    mock_msg.send_time = datetime(2026, 6, 1, 8, 0)
    mock_msg.channel = "wechat"
    mock_msg.message = "test message"
    mock_msg.description = None
    svc.schedule_message = AsyncMock(return_value=mock_msg)
    svc.list_pending_messages = AsyncMock(return_value=[])
    svc.cancel_message = AsyncMock(return_value=True)
    return svc


# ========== ScheduleMessageTool ==========


class TestScheduleMessage:
    @pytest.mark.asyncio
    async def test_email_without_subject_returns_error(self, schedule_tool):
        result = await schedule_tool._arun(
            message="hello",
            send_time="2026-06-01T08:00:00",
            channel="email",
        )
        assert "需要提供subject" in result

    @pytest.mark.asyncio
    async def test_invalid_send_time_format(self, schedule_tool):
        result = await schedule_tool._arun(
            message="hello",
            send_time="not-a-date",
            channel="wechat",
        )
        assert "send_time格式无效" in result

    @pytest.mark.asyncio
    async def test_schedule_wechat_success(self, schedule_tool, mock_msg_service):
        with (
            patch.object(schedule_tool, "_get_service", return_value=mock_msg_service),
            patch.object(schedule_tool, "_get_timezone", return_value="Asia/Shanghai"),
            patch.object(
                schedule_tool, "_resolve_default_channel", return_value="wechat"
            ),
        ):
            result = await schedule_tool._arun(
                message="记得喝水",
                send_time="2026-06-01T10:00:00",
            )
        assert "定时消息已创建" in result
        assert "msg-001" in result
        mock_msg_service.schedule_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_schedule_email_resolves_address(
        self, schedule_tool, mock_msg_service
    ):
        mock_resolve = AsyncMock(return_value=None)
        with (
            patch.object(schedule_tool, "_resolve_email_address", mock_resolve),
            patch.object(schedule_tool, "_get_service", return_value=mock_msg_service),
            patch.object(schedule_tool, "_get_timezone", return_value="Asia/Shanghai"),
        ):
            result = await schedule_tool._arun(
                message="hello",
                send_time="2026-06-01T08:00:00",
                channel="email",
                subject="Test",
                email_address="user@example.com",
            )
        assert "定时消息已创建" in result
        mock_resolve.assert_called_once_with("user@example.com")

    @pytest.mark.asyncio
    async def test_schedule_email_address_error(self, schedule_tool):
        with patch.object(
            schedule_tool,
            "_resolve_email_address",
            return_value="错误: 需要提供收件邮箱地址",
        ):
            result = await schedule_tool._arun(
                message="hello",
                send_time="2026-06-01T08:00:00",
                channel="email",
                subject="Test",
            )
        assert "需要提供收件邮箱地址" in result

    def test_apply_description_both_channels(self, schedule_tool):
        schedule_tool._apply_description(has_wechat=True, has_email=True)
        assert "微信" in schedule_tool.description
        assert "邮件" in schedule_tool.description

    def test_apply_description_wechat_only(self, schedule_tool):
        schedule_tool._apply_description(has_wechat=True, has_email=False)
        assert "微信" in schedule_tool.description
        assert "邮件" not in schedule_tool.description


# ========== ListScheduledMessagesTool ==========


class TestListScheduledMessages:
    @pytest.mark.asyncio
    async def test_empty_pending(self, list_tool, mock_msg_service):
        with patch.object(list_tool, "_get_service", return_value=mock_msg_service):
            result = await list_tool._arun()
        assert "没有待发送" in result

    @pytest.mark.asyncio
    async def test_list_with_messages(self, list_tool, mock_msg_service):
        mock_msg = MagicMock()
        mock_msg.message_id = "msg-002"
        mock_msg.send_time = datetime(2026, 6, 2, 9, 0)
        mock_msg.channel = "wechat"
        mock_msg.message = "提醒内容"
        mock_msg.description = "备注"
        mock_msg_service.list_pending_messages = AsyncMock(return_value=[mock_msg])
        with patch.object(list_tool, "_get_service", return_value=mock_msg_service):
            result = await list_tool._arun()
        assert "msg-002" in result
        assert "1条" in result


# ========== CancelScheduledMessageTool ==========


class TestCancelScheduledMessage:
    @pytest.mark.asyncio
    async def test_cancel_success(self, cancel_tool, mock_msg_service):
        with patch.object(cancel_tool, "_get_service", return_value=mock_msg_service):
            result = await cancel_tool._arun(message_id="msg-001")
        assert "已取消" in result
        mock_msg_service.cancel_message.assert_called_once_with("msg-001")

    @pytest.mark.asyncio
    async def test_cancel_failure(self, cancel_tool, mock_msg_service):
        mock_msg_service.cancel_message = AsyncMock(return_value=False)
        with patch.object(cancel_tool, "_get_service", return_value=mock_msg_service):
            result = await cancel_tool._arun(message_id="msg-x")
        assert "失败" in result


# ========== ScheduledMessengerBase 共享逻辑 ==========


class TestScheduledMessengerBase:
    def test_get_timezone_default_on_failure(self, schedule_tool):
        with patch(
            "src.auth.auth_manager.get_auth_manager",
            side_effect=Exception("no auth"),
        ):
            tz = schedule_tool._get_timezone()
        assert tz == "Asia/Shanghai"

    def test_check_smtp_config_complete(self):
        with patch("src.config.smtp_config.is_configured", return_value=True):
            tool = ScheduleMessageTool(user_id="u1", thread_id="t1", agent_id="a1")
            assert tool._check_smtp_config() is True

    def test_check_smtp_config_incomplete(self, schedule_tool):
        with patch("src.config.smtp_config.is_configured", return_value=False):
            assert schedule_tool._check_smtp_config() is False

    def test_resolve_default_channel_wechat(self, schedule_tool):
        object.__setattr__(schedule_tool, "_available_channels", ["wechat", "email"])
        assert schedule_tool._resolve_default_channel() == "wechat"

    def test_resolve_default_channel_email_only(self, schedule_tool):
        object.__setattr__(schedule_tool, "_available_channels", ["email"])
        assert schedule_tool._resolve_default_channel() == "email"

    @pytest.mark.asyncio
    async def test_resolve_email_new_address_saves(
        self, schedule_tool, mock_msg_service
    ):
        mock_cfg_svc = AsyncMock()
        mock_cfg_svc.get_config_for_channel = AsyncMock(return_value=None)
        mock_cfg_svc.upsert_channel_config = AsyncMock(return_value=MagicMock())
        with (
            patch(
                "src.storage.service.user_channel_config_service.get_user_channel_config_service",
                return_value=mock_cfg_svc,
            ),
            patch.object(schedule_tool, "_get_service", return_value=mock_msg_service),
        ):
            result = await schedule_tool._resolve_email_address("new@example.com")
        assert result is None
        mock_cfg_svc.upsert_channel_config.assert_called_once_with(
            channel_type="email",
            config={"email_address": "new@example.com"},
        )

    @pytest.mark.asyncio
    async def test_resolve_email_no_address_no_saved(self, schedule_tool):
        mock_cfg_svc = AsyncMock()
        mock_cfg_svc.get_config_for_channel = AsyncMock(return_value=None)
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            return_value=mock_cfg_svc,
        ):
            result = await schedule_tool._resolve_email_address(None)
        assert result is not None
        assert "需要提供收件邮箱地址" in result

    def test_load_shared_config_uses_tool_config_when_present(self):
        tool = ScheduleMessageTool(
            user_id="u1",
            thread_id="t1",
            agent_id="a1",
            smtp_config={"host": "h"},
        )
        cfg = tool._load_shared_config()
        assert cfg == {"smtp_config": {"host": "h"}}

    def test_load_shared_config_fallback_to_shared(self, schedule_tool):
        """子工具config为空时回退读 scheduled_messenger 共享配置."""
        with patch("src.config.tools_config.get_config") as mock_get:
            mock_tc = MagicMock()
            mock_shared = MagicMock()
            mock_shared.config = {"smtp_config": {"host": "h"}}
            mock_tc.get_internal_tool_config.return_value = mock_shared
            mock_get.return_value = mock_tc
            cfg = schedule_tool._load_shared_config()
        assert cfg == {"smtp_config": {"host": "h"}}
