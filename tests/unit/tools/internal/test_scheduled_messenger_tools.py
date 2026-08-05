"""定时消息子工具单元测试.

覆盖拆分后的四个子工具:
- ScheduleMessageWechatTool (schedule_message_wechat)
- ScheduleMessageEmailTool (schedule_message_email)
- ListScheduledMessagesTool (list_scheduled_messages)
- CancelScheduledMessageTool (cancel_scheduled_message)

以及 ScheduledMessageHelper 的公共逻辑.
Mock外部依赖: get_scheduled_message_service, get_user_channel_config_service, get_auth_manager.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.internal.cancel_scheduled_message_tool import CancelScheduledMessageTool
from src.tools.internal.list_scheduled_messages_tool import ListScheduledMessagesTool
from src.tools.internal.schedule_message_email_tool import ScheduleMessageEmailTool
from src.tools.internal.schedule_message_wechat_tool import ScheduleMessageWechatTool
from src.tools.shared.tool_runtime import inject_identity


def _mock_msg_helper(service, *, timezone="Asia/Shanghai", channels=None):
    """创建全配置 mock ScheduledMessageHelper."""
    h = MagicMock()
    h.get_service = AsyncMock(return_value=service)
    h.get_timezone.return_value = timezone
    h.resolve_email_address = AsyncMock(return_value=None)
    h.check_smtp_config.return_value = "email" in (channels or [])
    h.check_channels = AsyncMock(return_value=channels or ["wechat"])
    h.has_any_channel = AsyncMock(return_value=bool(channels))
    h.load_shared_config.return_value = {}
    return h


@pytest.fixture
def wechat_tool():
    tool = ScheduleMessageWechatTool()
    inject_identity(tool, "u1", "t1", "a1")
    return tool


@pytest.fixture
def email_tool():
    tool = ScheduleMessageEmailTool()
    inject_identity(tool, "u1", "t1", "a1")
    return tool


@pytest.fixture
def list_tool():
    tool = ListScheduledMessagesTool()
    inject_identity(tool, "u1", "t1", "a1")
    return tool


@pytest.fixture
def cancel_tool():
    tool = CancelScheduledMessageTool()
    inject_identity(tool, "u1", "t1", "a1")
    return tool


@pytest.fixture
def mock_msg_service():
    svc = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = "msg-001"
    mock_msg.send_time = datetime(2026, 6, 1, 0, 0)
    mock_msg.channel = "wechat"
    mock_msg.message = "test message"
    mock_msg.description = None
    svc.schedule_message = AsyncMock(return_value=mock_msg)
    svc.list_pending_messages = AsyncMock(return_value=[])
    svc.cancel_message = AsyncMock(return_value=True)
    return svc


# ========== ScheduleMessageWechatTool ==========


class TestScheduleMessageWechat:
    @pytest.mark.asyncio
    async def test_invalid_send_time_format(self, wechat_tool):
        result = await wechat_tool._arun(
            message="hello",
            send_time="not-a-date",
        )
        assert "send_time格式无效" in result

    @pytest.mark.asyncio
    async def test_schedule_success_with_timezone(self, wechat_tool, mock_msg_service):
        with patch.object(
            wechat_tool, "_get_helper", return_value=_mock_msg_helper(mock_msg_service)
        ):
            result = await wechat_tool._arun(
                message="记得喝水",
                send_time="2026-06-01T10:00:00+08:00",
            )
        assert "定时消息已创建" in result
        assert "msg-001" in result
        assert "wechat" in result
        mock_msg_service.schedule_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_schedule_success_naive_time(self, wechat_tool, mock_msg_service):
        """不带时区的 send_time 也能正常处理 (兼容, service 层按用户时区解释)."""
        with patch.object(
            wechat_tool, "_get_helper", return_value=_mock_msg_helper(mock_msg_service)
        ):
            result = await wechat_tool._arun(
                message="记得喝水",
                send_time="2026-06-01T10:00:00",
            )
        assert "定时消息已创建" in result

    @pytest.mark.asyncio
    async def test_schedule_utc_time(self, wechat_tool, mock_msg_service):
        with patch.object(
            wechat_tool, "_get_helper", return_value=_mock_msg_helper(mock_msg_service)
        ):
            result = await wechat_tool._arun(
                message="hello",
                send_time="2026-06-01T02:00:00Z",
            )
        assert "定时消息已创建" in result

    @pytest.mark.asyncio
    async def test_is_available_wechat_configured(self, wechat_tool):
        mock_helper = _mock_msg_helper(None, channels=["wechat"])
        with patch.object(wechat_tool, "_get_helper", return_value=mock_helper):
            assert await wechat_tool.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_wechat_not_configured(self, wechat_tool):
        mock_helper = _mock_msg_helper(None, channels=["email"])
        with patch.object(wechat_tool, "_get_helper", return_value=mock_helper):
            assert await wechat_tool.is_available() is False


# ========== ScheduleMessageEmailTool ==========


class TestScheduleMessageEmail:
    @pytest.mark.asyncio
    async def test_schedule_success(self, email_tool, mock_msg_service):
        mock_msg_service.schedule_message = AsyncMock(
            return_value=MagicMock(
                message_id="msg-002",
                send_time=datetime(2026, 6, 1, 0, 0),
                channel="email",
                message="hello",
                description=None,
            )
        )
        with patch.object(
            email_tool, "_get_helper", return_value=_mock_msg_helper(mock_msg_service)
        ):
            result = await email_tool._arun(
                message="hello",
                send_time="2026-06-01T08:00:00+08:00",
                subject="Test Subject",
                email_address="user@example.com",
            )
        assert "定时消息已创建" in result
        assert "email" in result

    @pytest.mark.asyncio
    async def test_email_address_error(self, email_tool):
        mock_helper = _mock_msg_helper(None)
        mock_helper.resolve_email_address = AsyncMock(
            return_value="错误: 需要提供收件邮箱地址"
        )
        with patch.object(email_tool, "_get_helper", return_value=mock_helper):
            result = await email_tool._arun(
                message="hello",
                send_time="2026-06-01T08:00:00+08:00",
                subject="Test",
            )
        assert "需要提供收件邮箱地址" in result

    @pytest.mark.asyncio
    async def test_invalid_send_time_format(self, email_tool):
        with patch.object(
            email_tool, "_get_helper", return_value=_mock_msg_helper(None)
        ):
            result = await email_tool._arun(
                message="hello",
                send_time="bad-time",
                subject="Test",
            )
        assert "send_time格式无效" in result

    @pytest.mark.asyncio
    async def test_is_available_smtp_configured(self, email_tool):
        mock_helper = _mock_msg_helper(None, channels=["wechat", "email"])
        mock_helper.check_smtp_config.return_value = True
        with patch.object(email_tool, "_get_helper", return_value=mock_helper):
            assert await email_tool.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_smtp_not_configured(self, email_tool):
        mock_helper = _mock_msg_helper(None, channels=["wechat"])
        mock_helper.check_smtp_config.return_value = False
        with patch.object(email_tool, "_get_helper", return_value=mock_helper):
            assert await email_tool.is_available() is False


# ========== ListScheduledMessagesTool ==========


class TestListScheduledMessages:
    @pytest.mark.asyncio
    async def test_empty_pending(self, list_tool, mock_msg_service):
        mock_helper = _mock_msg_helper(mock_msg_service)
        with patch.object(list_tool, "_get_helper", return_value=mock_helper):
            result = await list_tool._arun()
        assert "没有待发送" in result

    @pytest.mark.asyncio
    async def test_list_with_messages_shows_local_time(self, list_tool, mock_msg_service):
        mock_msg = MagicMock()
        mock_msg.message_id = "msg-002"
        mock_msg.send_time = datetime(2026, 6, 2, 1, 0)
        mock_msg.channel = "wechat"
        mock_msg.message = "提醒内容"
        mock_msg.description = "备注"
        mock_msg_service.list_pending_messages = AsyncMock(return_value=[mock_msg])
        mock_helper = _mock_msg_helper(mock_msg_service, timezone="Asia/Shanghai")
        with patch.object(list_tool, "_get_helper", return_value=mock_helper):
            result = await list_tool._arun()
        assert "msg-002" in result
        assert "1条" in result
        assert "09:00" in result
        assert "Asia/Shanghai" in result

    @pytest.mark.asyncio
    async def test_is_available_any_channel(self, list_tool):
        mock_helper = _mock_msg_helper(None, channels=["wechat"])
        with patch.object(list_tool, "_get_helper", return_value=mock_helper):
            assert await list_tool.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_no_channel(self, list_tool):
        mock_helper = _mock_msg_helper(None, channels=[])
        with patch.object(list_tool, "_get_helper", return_value=mock_helper):
            assert await list_tool.is_available() is False


# ========== CancelScheduledMessageTool ==========


class TestCancelScheduledMessage:
    @pytest.mark.asyncio
    async def test_cancel_success(self, cancel_tool, mock_msg_service):
        mock_helper = _mock_msg_helper(mock_msg_service)
        with patch.object(cancel_tool, "_get_helper", return_value=mock_helper):
            result = await cancel_tool._arun(message_id="msg-001")
        assert "已取消" in result
        mock_msg_service.cancel_message.assert_called_once_with("msg-001")

    @pytest.mark.asyncio
    async def test_cancel_failure(self, cancel_tool, mock_msg_service):
        mock_msg_service.cancel_message = AsyncMock(return_value=False)
        mock_helper = _mock_msg_helper(mock_msg_service)
        with patch.object(cancel_tool, "_get_helper", return_value=mock_helper):
            result = await cancel_tool._arun(message_id="msg-x")
        assert "失败" in result


# ========== ScheduledMessageHelper 共享逻辑 ==========


class TestScheduledMessageHelper:
    def test_get_timezone_default_on_failure(self, wechat_tool):
        with patch(
            "src.auth.auth_manager.get_auth_manager",
            side_effect=Exception("no auth"),
        ):
            tz = wechat_tool._get_helper().get_timezone()
        assert tz == "Asia/Shanghai"

    def test_check_smtp_config_complete(self, wechat_tool):
        with patch("src.config.smtp_config.is_configured", return_value=True):
            assert wechat_tool._get_helper().check_smtp_config() is True

    def test_check_smtp_config_incomplete(self, wechat_tool):
        with patch("src.config.smtp_config.is_configured", return_value=False):
            assert wechat_tool._get_helper().check_smtp_config() is False

    @pytest.mark.asyncio
    async def test_has_any_channel_wechat(self, wechat_tool):
        mock_helper = _mock_msg_helper(None, channels=["wechat"])
        with patch.object(wechat_tool, "_get_helper", return_value=mock_helper):
            assert await wechat_tool._get_helper().has_any_channel() is True

    @pytest.mark.asyncio
    async def test_resolve_email_new_address_saves(self):
        from src.tools.internal.scheduled_message_helper import ScheduledMessageHelper

        mock_cfg_svc = AsyncMock()
        mock_cfg_svc.get_config_for_channel = AsyncMock(return_value=None)
        mock_cfg_svc.upsert_channel_config = AsyncMock(return_value=MagicMock())
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            return_value=mock_cfg_svc,
        ):
            helper = ScheduledMessageHelper("u1", "t1", "a1")
            result = await helper.resolve_email_address("new@example.com")
        assert result is None
        mock_cfg_svc.upsert_channel_config.assert_called_once_with(
            channel_type="email",
            config={"email_address": "new@example.com"},
        )

    @pytest.mark.asyncio
    async def test_resolve_email_no_address_no_saved(self, wechat_tool):
        mock_cfg_svc = AsyncMock()
        mock_cfg_svc.get_config_for_channel = AsyncMock(return_value=None)
        with patch(
            "src.storage.service.user_channel_config_service.get_user_channel_config_service",
            return_value=mock_cfg_svc,
        ):
            result = await wechat_tool._get_helper().resolve_email_address(None)
        assert result is not None
        assert "需要提供收件邮箱地址" in result

    def test_load_shared_config_uses_tool_config_when_present(self):
        from src.tools.internal.scheduled_message_helper import ScheduledMessageHelper

        helper = ScheduledMessageHelper("u1", "t1", "a1")
        helper._tool_config = {"smtp_config": {"host": "h"}}
        cfg = helper.load_shared_config()
        assert cfg == {"smtp_config": {"host": "h"}}

    def test_load_shared_config_fallback_to_shared(self, wechat_tool):
        """子工具config为空时回退读 scheduled_messenger 共享配置."""
        with patch("src.config.tools_config.get_config") as mock_get:
            mock_tc = MagicMock()
            mock_shared = MagicMock()
            mock_shared.config = {"smtp_config": {"host": "h"}}
            mock_tc.get_internal_tool_config.return_value = mock_shared
            mock_get.return_value = mock_tc
            cfg = wechat_tool._get_helper().load_shared_config()
        assert cfg == {"smtp_config": {"host": "h"}}
