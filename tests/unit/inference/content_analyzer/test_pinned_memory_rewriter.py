"""PinnedMemoryRewriter 单元测试.

Mock invoke_with_fallback, 验证 mode 切换 prompt / messages 构造 / JSON 解析 / needs_update 短路.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.inference.content_analyzer.pinned_memory_rewriter import (
    PinnedMemoryRewriter,
)


@pytest.fixture
def rewriter() -> PinnedMemoryRewriter:
    return PinnedMemoryRewriter(
        model_id="aliyun-token-plan:qwen3.7-max",
        model_params={"temperature": 0.3},
    )


@pytest.fixture
def base_messages() -> list:
    return [
        SystemMessage(content="你是个人助手"),
        HumanMessage(content="我叫张三, 住在武汉"),
    ]


def _mock_response(content: str):
    """构造模拟的 LLM response."""
    mock = type("R", (), {"content": content})()
    return mock


@pytest.mark.asyncio
async def test_rewrite_needs_update_true(rewriter, base_messages):
    """needs_update=true 时返回 content."""
    json_out = '{"needs_update": true, "content": "用户名张三\\n居住地武汉"}'
    with patch(
        "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
        new_callable=AsyncMock,
        return_value=_mock_response(json_out),
    ) as mock_invoke:
        result = await rewriter.rewrite(
            base_messages,
            response="你好张三!",
            current_memory="(空)",
            mode="local",
        )

    assert result.needs_update is True
    assert "张三" in result.content
    mock_invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_rewrite_needs_update_false_short_circuits(rewriter, base_messages):
    """needs_update=false 时 content 为空."""
    json_out = '{"needs_update": false}'
    with patch(
        "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
        new_callable=AsyncMock,
        return_value=_mock_response(json_out),
    ):
        result = await rewriter.rewrite(
            base_messages,
            response="好的",
            current_memory="用户名张三",
            mode="local",
        )

    assert result.needs_update is False
    assert result.content == ""


@pytest.mark.asyncio
async def test_rewrite_appends_response_and_instruction(rewriter, base_messages):
    """验证完整 messages = 快照 + AIMessage(response) + HumanMessage(指令)."""
    with patch(
        "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
        new_callable=AsyncMock,
        return_value=_mock_response('{"needs_update": false}'),
    ) as mock_invoke:
        await rewriter.rewrite(
            base_messages,
            response="回复内容",
            current_memory="旧记忆",
            mode="local",
        )

    sent_messages = mock_invoke.call_args[0][0]
    assert len(sent_messages) == len(base_messages) + 2
    assert isinstance(sent_messages[-2], AIMessage)
    assert sent_messages[-2].content == "回复内容"
    assert isinstance(sent_messages[-1], HumanMessage)
    assert "旧记忆" in sent_messages[-1].content


@pytest.mark.asyncio
async def test_rewrite_mode_local_uses_local_prompt(rewriter, base_messages):
    """local mode 使用 local prompt (含'用户是谁'判据)."""
    with patch(
        "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
        new_callable=AsyncMock,
        return_value=_mock_response('{"needs_update": false}'),
    ) as mock_invoke:
        await rewriter.rewrite(
            base_messages,
            response="ok",
            current_memory="",
            mode="local",
        )

    instruction = mock_invoke.call_args[0][0][-1].content
    assert "用户是谁" in instruction or "身份事实" in instruction


@pytest.mark.asyncio
async def test_rewrite_mode_simple_uses_simple_prompt(rewriter, base_messages):
    """simple mode 使用 simple prompt (含'领域洞察'判据)."""
    with patch(
        "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
        new_callable=AsyncMock,
        return_value=_mock_response('{"needs_update": false}'),
    ) as mock_invoke:
        await rewriter.rewrite(
            base_messages,
            response="ok",
            current_memory="",
            mode="simple",
        )

    instruction = mock_invoke.call_args[0][0][-1].content
    assert "领域洞察" in instruction or "可复用经验" in instruction


@pytest.mark.asyncio
async def test_rewrite_unknown_mode_raises(rewriter, base_messages):
    """未知 mode 抛 ValueError."""
    with pytest.raises(ValueError, match="未知 mode"):
        await rewriter.rewrite(
            base_messages,
            response="ok",
            current_memory="",
            mode="invalid",
        )


@pytest.mark.asyncio
async def test_rewrite_json_parse_error_returns_no_update(rewriter, base_messages):
    """JSON 解析失败时安全降级 (needs_update=False)."""
    with patch(
        "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
        new_callable=AsyncMock,
        return_value=_mock_response("这不是JSON"),
    ):
        result = await rewriter.rewrite(
            base_messages,
            response="ok",
            current_memory="",
            mode="local",
        )

    assert result.needs_update is False


@pytest.mark.asyncio
async def test_rewrite_json_embedded_in_text(rewriter, base_messages):
    """JSON 嵌在文本中时用正则提取."""
    raw = '好的, 这是结果: {"needs_update": true, "content": "测试"} 完成'
    with patch(
        "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
        new_callable=AsyncMock,
        return_value=_mock_response(raw),
    ):
        result = await rewriter.rewrite(
            base_messages,
            response="ok",
            current_memory="",
            mode="local",
        )

    assert result.needs_update is True
    assert result.content == "测试"
