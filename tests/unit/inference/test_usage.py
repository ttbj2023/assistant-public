"""用量采集工具单元测试."""

from __future__ import annotations

import asyncio
import inspect
import threading
from unittest.mock import patch

import pytest
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models.fake import FakeListLLM
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from src.core.context import UserContext, reset_user_context, set_user_context
from src.inference import usage as usage_mod
from src.inference.usage import UsageTrackingCallback, extract_llm_usage


def test_extract_llm_usage_from_ai_message_usage_metadata() -> None:
    """应优先提取 AIMessage.usage_metadata."""
    message = AIMessage(
        content="你好",
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "input_token_details": {
                "cache_read": 3,
                "cache_creation": 2,
            },
            "output_token_details": {"reasoning": 4},
        },
    )
    result = LLMResult(generations=[[ChatGeneration(message=message)]])

    usage, raw, _metadata = extract_llm_usage(result)

    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 5
    assert usage["total_tokens"] == 15
    assert usage["cache_read_tokens"] == 3
    assert usage["cache_creation_tokens"] == 2
    assert usage["reasoning_tokens"] == 4
    assert usage["accuracy"] == "exact"
    assert raw is not None


def test_extract_llm_usage_from_openai_compatible_llm_output() -> None:
    """应提取 OpenAI-compatible token_usage."""
    result = LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content="ok"))]],
        llm_output={
            "model": "doubao:test-model",
            "token_usage": {
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 6},
                "completion_tokens_details": {"reasoning_tokens": 7},
            },
        },
    )

    usage, _raw, _metadata = extract_llm_usage(result)

    assert usage["input_tokens"] == 12
    assert usage["output_tokens"] == 8
    assert usage["total_tokens"] == 20
    assert usage["cache_read_tokens"] == 6
    assert usage["reasoning_tokens"] == 7
    assert usage["accuracy"] == "exact"


def test_extract_llm_usage_estimates_missing_usage() -> None:
    """缺失 usage 时应估算输出 token 并标记 estimated."""
    result = LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content="hello world"))]],
    )

    usage, raw, _metadata = extract_llm_usage(result)

    assert usage["input_tokens"] is None
    assert isinstance(usage["output_tokens"], int)
    assert usage["total_tokens"] == usage["output_tokens"]
    assert usage["accuracy"] == "estimated"
    assert raw is None


@pytest.mark.asyncio
async def test_usage_callback_is_async_handler() -> None:
    """UsageTrackingCallback 必须是 async callback handler.

    回归保护: 同步 (BaseCallbackHandler) callback 经 LangChain
    AsyncCallbackManager 用 run_in_executor 调度到线程池, executor 线程
    没有 running loop, record_usage_from_context 内 asyncio.get_running_loop()
    抛 RuntimeError 而静默跳过, 导致 llm_chat 用量完全丢失.
    """
    assert issubclass(UsageTrackingCallback, AsyncCallbackHandler)
    assert inspect.iscoroutinefunction(UsageTrackingCallback.on_llm_end)


@pytest.mark.asyncio
async def test_usage_callback_records_llm_chat_in_main_loop() -> None:
    """async 调用链下应在主 loop 线程落库 llm_chat 用量.

    若改回同步 handler, on_llm_end 会在 asyncio executor 线程执行,
    record_usage_from_context 因无 running loop 静默跳过, _persist_usage
    不会被调用.
    """
    persisted: list[tuple[str, str]] = []

    async def spy_persist(data: object) -> None:
        persisted.append((data.operation, threading.current_thread().name))  # type: ignore[attr-defined]

    token = set_user_context(
        UserContext(user_id="U", thread_id="t", agent_id="a"),
    )
    try:
        with patch.object(usage_mod, "_persist_usage", spy_persist):
            model = FakeListLLM(
                responses=["ok"],
                callbacks=[UsageTrackingCallback()],
            )
            await model.ainvoke("hi")
            await asyncio.sleep(0.05)
    finally:
        reset_user_context(token)

    assert len(persisted) == 1
    operation, thread = persisted[0]
    assert operation == "llm_chat"
    # 落库必须在主 loop 线程, 而非 asyncio executor 线程 (asyncio_0)
    assert "asyncio" not in thread


@pytest.mark.asyncio
async def test_usage_callback_records_llm_duration_ms() -> None:
    """on_llm_start/on_llm_end 配对应产生非 None 的 duration_ms.

    回归: on_llm_end 的 kwargs 不含 start_time (LangChain BaseCallbackHandler
    签名只有 response/run_id/parent_run_id/tags), 旧实现 duration_ms 永远
    None; 改为 on_llm_start/on_chat_model_start 自行记录开始时刻后,
    duration_ms 应为非负整数.
    """
    persisted: list[object] = []

    async def spy_persist(data: object) -> None:
        persisted.append(data)

    token = set_user_context(
        UserContext(user_id="U", thread_id="t", agent_id="a"),
    )
    try:
        with patch.object(usage_mod, "_persist_usage", spy_persist):
            model = FakeListLLM(
                responses=["ok"],
                callbacks=[UsageTrackingCallback()],
            )
            await model.ainvoke("hi")
            await asyncio.sleep(0.05)
    finally:
        reset_user_context(token)

    assert len(persisted) == 1
    duration_ms = persisted[0].duration_ms  # type: ignore[attr-defined]
    assert duration_ms is not None
    assert isinstance(duration_ms, int)
    assert duration_ms >= 0
