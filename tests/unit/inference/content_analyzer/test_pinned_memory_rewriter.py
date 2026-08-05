"""PinnedMemoryRewriter 单元测试.

Mock invoke_with_fallback, 验证 mode 切换 prompt / messages 构造 / JSON 解析 / needs_update 短路.
"""

from __future__ import annotations

import json
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


def test_rewrite_prompt_uses_capacity_placeholders():
    """prompt 模板用占位符引用容量约束, 杜绝与代码常量漂移.

    rewriter 的 prompt 文本与 service 的 check_capacity 共享同一常量来源,
    改常量时 prompt 自动同步, 无需人工修改两处.
    """
    from src.inference.content_analyzer.pinned_memory_rewriter import (
        MAX_LINES,
        MAX_TOTAL_LENGTH,
        PinnedMemoryRewriter,
    )

    for template in (
        PinnedMemoryRewriter.LOCAL_REWRITE_PROMPT,
        PinnedMemoryRewriter.SIMPLE_REWRITE_PROMPT,
    ):
        assert "{max_lines}" in template, "prompt 须用 {max_lines} 占位符"
        assert "{max_total_length}" in template, "prompt 须用 {max_total_length} 占位符"
        rendered = template.format(
            current_memory="(空)",
            max_lines=MAX_LINES,
            max_total_length=MAX_TOTAL_LENGTH,
        )
        assert f"不超过{MAX_LINES}行/{MAX_TOTAL_LENGTH}字" in rendered


def _over_budget_content(lines: int = 25) -> str:
    """生成超限内容 (超过 20 行)."""
    return "\n".join(
        f"条目{i}: 这是一条很长的记忆内容用于测试超限" for i in range(lines)
    )


class TestBudgetRetry:
    """预算超限重试机制测试."""

    @pytest.mark.asyncio
    async def test_within_budget_no_retry(self, rewriter, base_messages):
        """合规输出不触发重试, 仅调用一次 LLM."""
        json_out = '{"needs_update": true, "content": "用户名张三\\n居住地武汉"}'
        with patch(
            "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
            new_callable=AsyncMock,
            return_value=_mock_response(json_out),
        ) as mock_invoke:
            result = await rewriter.rewrite(
                base_messages, response="ok", current_memory="", mode="local"
            )

        assert result.needs_update is True
        assert "张三" in result.content
        assert mock_invoke.await_count == 1

    @pytest.mark.asyncio
    async def test_over_budget_triggers_retry_success(self, rewriter, base_messages):
        """超限输出触发重试, 第二次合规则返回精简结果."""
        over_content = _over_budget_content(25)
        first_out = (
            '{"needs_update": true, "content": ' + json.dumps(over_content) + "}"
        )
        trimmed = "用户名张三\\n居住地武汉"
        second_out = '{"needs_update": true, "content": "' + trimmed + '"}'

        with patch(
            "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
            new_callable=AsyncMock,
            side_effect=[_mock_response(first_out), _mock_response(second_out)],
        ) as mock_invoke:
            result = await rewriter.rewrite(
                base_messages, response="ok", current_memory="", mode="local"
            )

        assert result.needs_update is True
        assert result.content == "用户名张三\n居住地武汉"
        assert mock_invoke.await_count == 2

    @pytest.mark.asyncio
    async def test_retry_messages_include_original_output(
        self, rewriter, base_messages
    ):
        """重试时 messages 包含原始输出 (AIMessage) + 报错反馈 (HumanMessage)."""
        over_content = _over_budget_content(25)
        first_out = (
            '{"needs_update": true, "content": ' + json.dumps(over_content) + "}"
        )
        second_out = '{"needs_update": true, "content": "精简后"}'

        with patch(
            "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
            new_callable=AsyncMock,
            side_effect=[_mock_response(first_out), _mock_response(second_out)],
        ) as mock_invoke:
            await rewriter.rewrite(
                base_messages, response="ok", current_memory="", mode="local"
            )

        retry_messages = mock_invoke.call_args_list[1][0][0]
        # 原始 messages(2) + AIMessage(response) + HumanMessage(instruction) + raw + error
        assert len(retry_messages) == len(base_messages) + 4
        assert isinstance(retry_messages[-2], type(retry_messages[-2]))
        assert "超出了记忆容量限制" in retry_messages[-1].content

    @pytest.mark.asyncio
    async def test_both_attempts_over_budget_accepts_second(
        self, rewriter, base_messages, caplog
    ):
        """两次都超限时接受第二次结果并记录 warning."""
        over1 = _over_budget_content(25)
        over2 = _over_budget_content(22)
        first_out = '{"needs_update": true, "content": ' + json.dumps(over1) + "}"
        second_out = '{"needs_update": true, "content": ' + json.dumps(over2) + "}"

        with (
            patch(
                "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
                new_callable=AsyncMock,
                side_effect=[_mock_response(first_out), _mock_response(second_out)],
            ),
            caplog.at_level(
                "WARNING",
                logger="src.inference.content_analyzer.pinned_memory_rewriter",
            ),
        ):
            result = await rewriter.rewrite(
                base_messages, response="ok", current_memory="", mode="local"
            )

        assert result.needs_update is True
        assert result.content == over2
        assert "重试后仍超限" in caplog.text

    @pytest.mark.asyncio
    async def test_needs_update_false_skips_budget_check(self, rewriter, base_messages):
        """needs_update=false 时不检查预算, 不触发重试."""
        json_out = '{"needs_update": false}'
        with patch(
            "src.inference.content_analyzer.pinned_memory_rewriter.invoke_with_fallback",
            new_callable=AsyncMock,
            return_value=_mock_response(json_out),
        ) as mock_invoke:
            result = await rewriter.rewrite(
                base_messages, response="ok", current_memory="", mode="local"
            )

        assert result.needs_update is False
        assert mock_invoke.await_count == 1

    def test_within_budget_boundary(self):
        """_within_budget 边界: 恰好 20 行 / 800 字合规."""
        from src.inference.content_analyzer.pinned_memory_rewriter import (
            MAX_LINES,
            MAX_TOTAL_LENGTH,
        )

        exact_lines = "\n".join(f"行{i}" for i in range(MAX_LINES))
        assert PinnedMemoryRewriter._within_budget(exact_lines) is True

        over_lines = "\n".join(f"行{i}" for i in range(MAX_LINES + 1))
        assert PinnedMemoryRewriter._within_budget(over_lines) is False

        exact_chars = "a" * MAX_TOTAL_LENGTH
        assert PinnedMemoryRewriter._within_budget(exact_chars) is True

        over_chars = "a" * (MAX_TOTAL_LENGTH + 1)
        assert PinnedMemoryRewriter._within_budget(over_chars) is False

    def test_budget_error_contains_specific_numbers(self):
        """报错反馈包含具体超限数字."""
        content = "\n".join(f"条目{i}: 内容" for i in range(25))
        error = PinnedMemoryRewriter._budget_error(content)
        assert "25行" in error
        assert f"{len(content)}字" in error
        assert "20行" in error
        assert "800字" in error
