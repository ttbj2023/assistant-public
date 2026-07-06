"""模型用量采集与落库.

依赖说明: 本模块对 storage.service 的依赖 (采集即落库, 天然耦合) 经架构
评估保留, 详见 AGENTS.md "分层依赖总览 - 已知的语义合理交叉依赖". 后续
依赖审计请勿重复标记为违规.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, override

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

from src.core.context import (
    get_user_context_or_none,
    replace_user_context,
    reset_user_context,
)
from src.storage.models.usage import UsageRecordCreate
from src.storage.service import create_usage_service
from src.utils.async_utils import spawn_background_task
from src.utils.token_utils import TokenEstimator

logger = logging.getLogger(__name__)

_TOKEN_ESTIMATOR = TokenEstimator()


@contextmanager
def usage_source(source: str) -> Iterator[None]:
    """临时设置当前调用链的 usage_source."""
    ctx = get_user_context_or_none()
    if ctx is None:
        yield
        return

    token = replace_user_context(usage_source=source)
    try:
        yield
    finally:
        reset_user_context(token)


def record_usage_from_context(
    *,
    operation: str,
    provider: str | None = None,
    model_id: str | None = None,
    unit_type: str = "token",
    request_count: int = 1,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    accuracy: str = "unknown",
    success: bool = True,
    duration_ms: int | None = None,
    raw_usage: dict | None = None,
    metadata: dict | None = None,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    external_job_id: str | None = None,
    usage_source_override: str | None = None,
) -> None:
    """从当前 UserContext 记录用量, 无上下文时跳过."""
    data = _build_usage_record_from_context(
        operation=operation,
        provider=provider,
        model_id=model_id,
        unit_type=unit_type,
        request_count=request_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        reasoning_tokens=reasoning_tokens,
        accuracy=accuracy,
        success=success,
        duration_ms=duration_ms,
        raw_usage=raw_usage,
        metadata=metadata,
        run_id=run_id,
        parent_run_id=parent_run_id,
        external_job_id=external_job_id,
        usage_source_override=usage_source_override,
    )
    if data is None:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("跳过用量记录: 当前线程无事件循环 operation=%s", operation)
        return

    if loop.is_closed():
        return

    spawn_background_task(_persist_usage(data))


async def arecord_usage_from_context(
    *,
    operation: str,
    provider: str | None = None,
    model_id: str | None = None,
    unit_type: str = "token",
    request_count: int = 1,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    accuracy: str = "unknown",
    success: bool = True,
    duration_ms: int | None = None,
    raw_usage: dict | None = None,
    metadata: dict | None = None,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    external_job_id: str | None = None,
    usage_source_override: str | None = None,
) -> None:
    """从当前 UserContext 异步记录用量, 无上下文时跳过."""
    data = _build_usage_record_from_context(
        operation=operation,
        provider=provider,
        model_id=model_id,
        unit_type=unit_type,
        request_count=request_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        reasoning_tokens=reasoning_tokens,
        accuracy=accuracy,
        success=success,
        duration_ms=duration_ms,
        raw_usage=raw_usage,
        metadata=metadata,
        run_id=run_id,
        parent_run_id=parent_run_id,
        external_job_id=external_job_id,
        usage_source_override=usage_source_override,
    )
    if data is None:
        return
    await _persist_usage(data)


def _build_usage_record_from_context(
    *,
    operation: str,
    provider: str | None,
    model_id: str | None,
    unit_type: str,
    request_count: int,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    cache_read_tokens: int | None,
    cache_creation_tokens: int | None,
    reasoning_tokens: int | None,
    accuracy: str,
    success: bool,
    duration_ms: int | None,
    raw_usage: dict | None,
    metadata: dict | None,
    run_id: str | None,
    parent_run_id: str | None,
    external_job_id: str | None,
    usage_source_override: str | None,
) -> UsageRecordCreate | None:
    ctx = get_user_context_or_none()
    if ctx is None:
        logger.debug("跳过用量记录: UserContext 未设置 operation=%s", operation)
        return None

    data = UsageRecordCreate(
        user_id=ctx.user_id,
        thread_id=ctx.thread_id,
        agent_id=ctx.agent_id,
        round_number=ctx.round_number,
        request_id=ctx.request_id,
        operation=operation,
        usage_source=usage_source_override or ctx.usage_source,
        provider=provider,
        model_id=model_id,
        run_id=run_id,
        parent_run_id=parent_run_id,
        external_job_id=external_job_id,
        unit_type=unit_type,  # type: ignore[arg-type]
        request_count=request_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        reasoning_tokens=reasoning_tokens,
        accuracy=accuracy,  # type: ignore[arg-type]
        success=success,
        duration_ms=duration_ms,
        raw_usage=raw_usage,
        metadata=metadata,
    )
    return data


async def _persist_usage(data: UsageRecordCreate) -> None:
    try:
        service = await create_usage_service(data.user_id)
        await service.record_usage(data)
    except Exception as e:
        logger.warning("用量记录写入失败(非阻塞): %s", e)


class UsageTrackingCallback(AsyncCallbackHandler):
    """LangChain LLM 用量采集 callback.

    必须用 async callback: LangChain 的 AsyncCallbackManager 会把同步
    (BaseCallbackHandler) handler 经 run_in_executor 调度到线程池,
    executor 线程没有 running loop, record_usage_from_context 内的
    asyncio.get_running_loop() 抛 RuntimeError 而静默跳过, 导致 LLM
    用量 (llm_chat) 完全丢失. async handler 在主 loop 的 task 里被 await,
    running loop 可用, 用量才能正常落库.

    LLM 延迟由 on_chat_model_start/on_llm_start 与 on_llm_end 配对计时
    (run_id 关联): LangChain 的 on_llm_end kwargs 不含 start_time
    (BaseCallbackHandler 签名只有 response/run_id/parent_run_id/tags),
    无法从 kwargs 取开始时刻. ChatModel 触发 on_chat_model_start,
    非 chat 的 LLM 触发 on_llm_start, 二者皆记录开始时刻供 on_llm_end 计算.
    """

    def __init__(self) -> None:
        # run_id -> 开始时刻; on_llm_end/on_llm_error 时 pop 清理
        self._llm_start_times: dict[Any, float] = {}

    def _record_start(self, run_id: Any) -> None:
        if run_id is not None:
            self._llm_start_times[run_id] = time.time()

    @override
    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        self._record_start(run_id)

    @override
    async def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        self._record_start(run_id)

    @override
    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        started_at = self._llm_start_times.pop(run_id, None)
        duration_ms = _duration_ms(started_at)
        usage, raw_usage, metadata = extract_llm_usage(response)

        model_id = _model_id_from_response(response, raw_usage, kwargs.get("metadata"))
        provider = _provider_from_model_id(model_id)

        record_usage_from_context(
            operation="llm_chat",
            provider=provider,
            model_id=model_id,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            total_tokens=usage.get("total_tokens"),
            cache_read_tokens=usage.get("cache_read_tokens"),
            cache_creation_tokens=usage.get("cache_creation_tokens"),
            reasoning_tokens=usage.get("reasoning_tokens"),
            accuracy=usage.get("accuracy", "unknown"),
            duration_ms=duration_ms,
            raw_usage=raw_usage,
            metadata=metadata,
            run_id=str(run_id) if run_id else None,
            parent_run_id=str(parent_run_id) if parent_run_id else None,
        )

    @override
    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        # 失败不记录 usage (保持原行为); 仅清理计时槽避免泄漏
        self._llm_start_times.pop(run_id, None)


def get_usage_tracking_callback() -> UsageTrackingCallback:
    """获取进程级用量采集 callback."""
    return UsageTrackingCallback()


def extract_llm_usage(
    response: LLMResult,
) -> tuple[dict[str, int | str | None], dict | None, dict]:
    """从 LangChain LLMResult 提取 token 用量."""
    raw_candidates: list[dict] = []
    metadata: dict[str, Any] = {}

    if isinstance(response.llm_output, dict):
        metadata["llm_output"] = _jsonable(response.llm_output)
        for key in ("token_usage", "usage", "usage_metadata"):
            value = response.llm_output.get(key)
            if isinstance(value, dict):
                raw_candidates.append(value)

    for generations in response.generations:
        for generation in generations:
            message = getattr(generation, "message", None)
            if message is None:
                continue

            usage_metadata = getattr(message, "usage_metadata", None)
            if isinstance(usage_metadata, dict):
                raw_candidates.append(usage_metadata)

            response_metadata = getattr(message, "response_metadata", None)
            if isinstance(response_metadata, dict):
                metadata.setdefault("response_metadata", _jsonable(response_metadata))
                for key in ("token_usage", "usage", "usage_metadata"):
                    value = response_metadata.get(key)
                    if isinstance(value, dict):
                        raw_candidates.append(value)

    for candidate in raw_candidates:
        parsed = _normalize_token_usage(candidate)
        if parsed["total_tokens"] is not None or parsed["input_tokens"] is not None:
            parsed["accuracy"] = "exact"
            return parsed, candidate, metadata

    estimated = _estimate_from_generations(response)
    return estimated, None, metadata


def record_embedding_usage(
    *,
    provider: str,
    model_id: str,
    texts: list[str],
    raw_usage: dict | None,
    duration_ms: int | None,
    success: bool = True,
) -> None:
    """记录 embedding 用量."""
    input_tokens, total_tokens, accuracy = _embedding_usage_values(texts, raw_usage)

    record_usage_from_context(
        operation="embedding",
        provider=provider,
        model_id=model_id,
        input_tokens=input_tokens,
        output_tokens=0,
        total_tokens=total_tokens,
        accuracy=accuracy,
        success=success,
        duration_ms=duration_ms,
        raw_usage=raw_usage,
        metadata={"text_count": len(texts)},
    )


async def arecord_embedding_usage(
    *,
    provider: str,
    model_id: str,
    texts: list[str],
    raw_usage: dict | None,
    duration_ms: int | None,
    success: bool = True,
) -> None:
    """异步记录 embedding 用量."""
    input_tokens, total_tokens, accuracy = _embedding_usage_values(texts, raw_usage)

    await arecord_usage_from_context(
        operation="embedding",
        provider=provider,
        model_id=model_id,
        input_tokens=input_tokens,
        output_tokens=0,
        total_tokens=total_tokens,
        accuracy=accuracy,
        success=success,
        duration_ms=duration_ms,
        raw_usage=raw_usage,
        metadata={"text_count": len(texts)},
    )


def _embedding_usage_values(
    texts: list[str],
    raw_usage: dict | None,
) -> tuple[int | None, int | None, str]:
    if raw_usage:
        normalized = _normalize_token_usage(raw_usage)
        input_tokens = normalized["input_tokens"]
        total_tokens = normalized["total_tokens"]
        return (
            input_tokens if isinstance(input_tokens, int) else None,
            total_tokens if isinstance(total_tokens, int) else None,
            "exact",
        )

    input_tokens = _estimate_texts_tokens(texts)
    return input_tokens, input_tokens, "estimated"


def _normalize_token_usage(raw: dict) -> dict[str, int | str | None]:
    input_tokens = _first_int(
        raw,
        "input_tokens",
        "prompt_tokens",
        "input_token_count",
        "prompt_token_count",
    )
    output_tokens = _first_int(
        raw,
        "output_tokens",
        "completion_tokens",
        "output_token_count",
        "completion_token_count",
    )
    total_tokens = _first_int(raw, "total_tokens", "total_token_count")

    details = _nested_dict(raw, "input_token_details", "prompt_tokens_details")
    output_details = _nested_dict(
        raw,
        "output_token_details",
        "completion_tokens_details",
    )

    cache_read = _first_int(
        details,
        "cache_read",
        "cached_tokens",
        "cache_read_tokens",
    )
    cache_creation = _first_int(
        details,
        "cache_creation",
        "cache_creation_tokens",
    )
    reasoning_tokens = _first_int(
        output_details,
        "reasoning",
        "reasoning_tokens",
    )

    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "reasoning_tokens": reasoning_tokens,
        "accuracy": "unknown",
    }


def _estimate_from_generations(response: LLMResult) -> dict[str, int | str | None]:
    output_texts: list[str] = []
    for generations in response.generations:
        for generation in generations:
            text = getattr(generation, "text", "")
            if text:
                output_texts.append(str(text))
                continue
            message = getattr(generation, "message", None)
            if message is not None:
                output_texts.append(_content_to_text(getattr(message, "content", "")))

    output_tokens = _estimate_texts_tokens(output_texts) if output_texts else None
    return {
        "input_tokens": None,
        "output_tokens": output_tokens,
        "total_tokens": output_tokens,
        "cache_read_tokens": None,
        "cache_creation_tokens": None,
        "reasoning_tokens": None,
        "accuracy": "estimated" if output_tokens is not None else "unknown",
    }


def _estimate_texts_tokens(texts: list[str]) -> int:
    return sum(_TOKEN_ESTIMATOR.estimate_tokens(text) for text in texts)


def _first_int(source: dict, *keys: str) -> int | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _nested_dict(source: dict, *keys: str) -> dict:
    for key in keys:
        value = source.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


def _model_id_from_response(
    response: LLMResult,
    raw_usage: dict | None,
    callback_metadata: Any,
) -> str | None:
    if isinstance(callback_metadata, dict):
        model = callback_metadata.get("model") or callback_metadata.get("ls_model_name")
        if isinstance(model, str):
            return model

    if isinstance(response.llm_output, dict):
        model = response.llm_output.get("model") or response.llm_output.get(
            "model_name"
        )
        if isinstance(model, str):
            return model

    if raw_usage:
        model = raw_usage.get("model") or raw_usage.get("model_name")
        if isinstance(model, str):
            return model

    for generations in response.generations:
        for generation in generations:
            message = getattr(generation, "message", None)
            metadata = getattr(message, "response_metadata", None) if message else None
            if isinstance(metadata, dict):
                model = metadata.get("model") or metadata.get("model_name")
                if isinstance(model, str):
                    return model
    return None


def _provider_from_model_id(model_id: str | None) -> str | None:
    if not model_id:
        return None
    if ":" in model_id:
        return model_id.split(":", 1)[0]
    return None


def _duration_ms(started_at: Any) -> int | None:
    if isinstance(started_at, (int, float)):
        return max(int((time.time() - started_at) * 1000), 0)
    return None


def _jsonable(value: Any) -> Any:
    with contextlib.suppress(TypeError):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    return str(value)


__all__ = [
    "UsageTrackingCallback",
    "arecord_embedding_usage",
    "arecord_usage_from_context",
    "extract_llm_usage",
    "get_usage_tracking_callback",
    "record_embedding_usage",
    "record_usage_from_context",
    "usage_source",
]
