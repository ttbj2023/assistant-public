"""AsyncRoundDetailTool 单元测试.

测试按轮次号获取对话完整原文的工具逻辑: 参数验证, 服务调用, 结果格式化.
Mock 外部依赖: create_conversation_service.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.internal.async_round_detail_tool import AsyncRoundDetailTool


@pytest.fixture
def tool():
    return AsyncRoundDetailTool(user_id="u1", thread_id="t1", agent_id="a1")


@pytest.fixture
def mock_service():
    svc = AsyncMock()
    svc.get_conversations_by_rounds = AsyncMock(return_value=[])
    return svc


def _inject_service(tool, mock_service):
    """通过 mock _get_service 注入服务实例, 让源码自行缓存."""

    async def _fake_get_service():
        object.__setattr__(tool, "_conversation_service", mock_service)
        return mock_service

    return patch.object(tool, "_get_service", side_effect=_fake_get_service)


def _make_conv(round_number, user_message="", assistant_response=""):
    """创建 Mock ConversationIndex."""
    conv = MagicMock()
    conv.round_number = round_number
    conv.user_message = user_message
    conv.assistant_response = assistant_response
    conv.topic = None
    conv.summary = None
    conv.created_at = None
    return conv


class TestInit:
    """测试初始化."""

    def test_empty_user_id_raises(self):
        with pytest.raises(ValueError, match="用户ID不能为空"):
            AsyncRoundDetailTool(user_id="", thread_id="t1", agent_id="a1")

    def test_empty_thread_id_raises(self):
        with pytest.raises(ValueError, match="线程ID不能为空"):
            AsyncRoundDetailTool(user_id="u1", thread_id="", agent_id="a1")


class TestArun:
    """测试异步执行."""

    @pytest.mark.asyncio
    async def test_should_return_full_content_for_requested_rounds(
        self, tool, mock_service
    ):
        """请求的轮次应返回完整 user_message + assistant_response."""
        conv = _make_conv(5, "用户消息原文", "助手回复原文")
        conv.topic = "项目进度"
        conv.summary = "讨论了时间表"
        mock_service.get_conversations_by_rounds = AsyncMock(return_value=[conv])

        with _inject_service(tool, mock_service):
            result = await tool._arun(round_numbers=[5])

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["total_count"] == 1
        entry = parsed["results"][0]
        assert entry["round_number"] == 5
        assert entry["topic"] == "项目进度"
        assert "用户消息原文" in entry["content"]
        assert "助手回复原文" in entry["content"]

    @pytest.mark.asyncio
    async def test_should_preserve_requested_order(self, tool, mock_service):
        """结果顺序应跟随请求的 round_numbers 顺序."""
        convs = [
            _make_conv(10, "msg10", "resp10"),
            _make_conv(3, "msg3", "resp3"),
        ]
        mock_service.get_conversations_by_rounds = AsyncMock(return_value=convs)

        with _inject_service(tool, mock_service):
            result = await tool._arun(round_numbers=[3, 10])

        parsed = json.loads(result)
        rounds = [r["round_number"] for r in parsed["results"]]
        assert rounds == [3, 10]

    @pytest.mark.asyncio
    async def test_empty_round_numbers_returns_error(self, tool):
        """空轮次列表应返回失败."""
        result = await tool._arun(round_numbers=[])
        parsed = json.loads(result)
        assert parsed["success"] is False

    @pytest.mark.asyncio
    async def test_not_found_round_reported_in_not_found(self, tool, mock_service):
        """请求了但不存在的轮次应记录在 not_found 字段."""
        conv = _make_conv(5, "exists", "resp")
        mock_service.get_conversations_by_rounds = AsyncMock(return_value=[conv])

        with _inject_service(tool, mock_service):
            result = await tool._arun(round_numbers=[5, 999])

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["total_count"] == 1
        assert parsed["not_found"] == [999]

    @pytest.mark.asyncio
    async def test_all_not_found_returns_empty_results(self, tool, mock_service):
        """全部轮次都不存在时返回空结果(非报错)."""
        mock_service.get_conversations_by_rounds = AsyncMock(return_value=[])

        with _inject_service(tool, mock_service):
            result = await tool._arun(round_numbers=[999, 1000])

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["total_count"] == 0
        assert parsed["not_found"] == [999, 1000]

    @pytest.mark.asyncio
    async def test_service_unavailable_returns_error(self, tool):
        """服务初始化失败应返回失败."""
        with patch.object(
            tool, "_get_service", side_effect=RuntimeError("init failed")
        ):
            result = await tool._arun(round_numbers=[1])

        parsed = json.loads(result)
        assert parsed["success"] is False
