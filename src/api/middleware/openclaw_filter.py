"""OpenClaw 注入过滤中间件.

在请求到达路由处理器之前清理 OpenClaw Gateway 注入的内容:
- 移除 system/developer/custom 角色消息
- 清理 user 消息中的元数据注入
- 心跳请求短路返回, 不触发 LLM 调用
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Generator
from typing import TYPE_CHECKING, override

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from src.core.openclaw_filter import (
    OpenClawInboundContext,
    filter_openclaw_request,
    parse_openclaw_inbound,
)
from src.core.streaming import (
    create_stream_chunk,
    create_stream_final_chunk,
    generate_completion_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

logger = logging.getLogger(__name__)

HEARTBEAT_RESPONSE = "HEARTBEAT_OK"
TARGET_PATH = "/v1/chat/completions"

_INBOUND_CONTEXT_RE = re.compile(
    r"## Inbound Context \(trusted metadata\).*?```json\s*\n(\{.*?\})\s*\n```",
    re.DOTALL,
)


def _log_inbound_context(
    data: dict,
    ctx: OpenClawInboundContext,
) -> None:
    for msg in data.get("messages", []):
        if msg.get("role") != "system":
            continue
        content = msg.get("content", "")
        match = _INBOUND_CONTEXT_RE.search(content)
        if match:
            logger.info(
                "OpenClaw Inbound Context 原始: %s",
                match.group(1),
            )
            break
    logger.info(
        "OpenClaw Inbound Context 解析: account_id=%s, channel=%s, chat_id=%s",
        ctx.account_id,
        ctx.channel,
        ctx.chat_id,
    )


class OpenClawFilterMiddleware(BaseHTTPMiddleware):
    """OpenClaw 注入过滤中间件."""

    def __init__(self, app: FastAPI) -> None:
        super().__init__(app)

    @override
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method != "POST" or request.url.path != TARGET_PATH:
            return await call_next(request)

        try:
            body = await request.body()
            if not body:
                return await call_next(request)

            data = json.loads(body)

            ctx = parse_openclaw_inbound(data)
            request.state.is_openclaw = ctx is not None

            if ctx is not None:
                request.state.openclaw_context = ctx
                _log_inbound_context(data, ctx)

            filtered, is_heartbeat = filter_openclaw_request(data)

            if is_heartbeat:
                logger.debug("OpenClaw 心跳请求, 短路返回 HEARTBEAT_OK")
                return _build_heartbeat_response(data.get("model", "unknown"))

            filtered_body = json.dumps(filtered, ensure_ascii=False).encode("utf-8")
            request._body = filtered_body

            orig_count = len(data.get("messages", []))
            clean_count = len(filtered.get("messages", []))
            if orig_count != clean_count:
                logger.debug("OpenClaw 过滤: %d -> %d 条消息", orig_count, clean_count)

        except (json.JSONDecodeError, KeyError, TypeError):
            logger.debug("非 JSON 请求体或格式异常, 跳过 OpenClaw 过滤")
        except Exception as e:
            logger.warning("OpenClaw 过滤异常, 继续处理原始请求: %s", e)

        return await call_next(request)


def _build_heartbeat_response(model: str) -> StreamingResponse:
    """构造心跳短路的 SSE 流式响应.

    Args:
        model: 模型名称

    Returns:
        SSE 格式的 StreamingResponse, 内容为 HEARTBEAT_OK

    """

    def _generate() -> Generator[str, None, None]:
        completion_id = generate_completion_id()
        created = int(time.time())

        yield create_stream_chunk(
            completion_id=completion_id,
            created=created,
            model=model,
            content=HEARTBEAT_RESPONSE,
        )

        yield create_stream_final_chunk(
            completion_id=completion_id,
            created=created,
            model=model,
        )

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
    )
