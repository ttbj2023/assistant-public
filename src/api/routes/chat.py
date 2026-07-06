"""OpenAI兼容的聊天API路由 - 标准JSON格式版本."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.agent.manager import get_agent, list_agents
from src.core.path_resolver import get_user_path_resolver
from src.core.streaming import (
    create_stream_chunk,
    create_stream_error_chunk,
    create_stream_final_chunk,
    generate_completion_id,
)
from src.core.types import MessageContent
from src.inference.llm.definitions.model_registry import get_model
from src.session.session_queue import SessionMessageQueue
from src.utils.async_utils import spawn_background_task
from src.utils.token_utils import TokenEstimator

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatMessage(BaseModel):
    """聊天消息类型 - 支持多模态内容 (OpenAI标准格式)."""

    role: Literal["user", "assistant", "system"] = Field(..., description="消息角色")
    content: MessageContent = Field(..., description="消息内容,支持字符串或内容块列表")
    name: str | None = Field(None, max_length=100)


class ChatCompletionRequest(BaseModel):
    """聊天完成请求类型 - 支持 Agent 选择."""

    messages: list[ChatMessage] = Field(..., min_length=1)
    model: str = Field(
        "personal-assistant",
        description="Agent ID,可通过 /v1/models 获取可用列表",
    )
    stream: bool = Field(default=False, description="是否启用流式响应")


class ChatCompletionResponse(BaseModel):
    """聊天完成响应类型."""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[dict[str, Any]]
    usage: dict[str, int | None] = Field(default_factory=dict)


# ========== 辅助函数 ==========


def _extract_content_with_images(
    content: MessageContent,
) -> tuple[str, list[dict]]:
    """从多模态内容中提取文本和图片.

    Args:
        content: 消息内容(字符串或内容块列表)

    Returns:
        (文本内容, 图片数据列表)

    Raises:
        ValueError: 内容格式无效

    """
    if isinstance(content, str):
        # 纯文本消息
        return content, []

    if isinstance(content, list):
        # 多模态消息
        text_parts = []
        images = []

        for block in content:
            if block.type == "text" and block.text:
                text_parts.append(block.text)
            elif block.type == "image_url" and block.image_url and block.image_url.url:
                # 解析Base64图片
                img_data = _parse_base64_image(block.image_url.url)
                if img_data:
                    images.append(img_data)

        user_text = "\n".join(text_parts) if text_parts else ""
        return user_text, images

    raise ValueError(f"不支持的内容类型: {type(content)}")


def _parse_base64_image(image_url: str) -> dict | None:
    """解析Base64格式的图片URL.

    Args:
        image_url: data URL格式 (data:image/jpeg;base64,...)

    Returns:
        {"data": bytes, "mime_type": str} 或 None

    """
    # 匹配 data URL格式
    pattern = r"^data:(image/[a-z+]+);base64,(.+)$"
    match = re.match(pattern, image_url)

    if not match:
        logger.warning(f"无效的图片URL格式: {image_url[:50]}...")
        return None

    mime_type = match.group(1)
    base64_data = match.group(2)

    try:
        # 解码Base64
        image_data = base64.b64decode(base64_data)
        return {"data": image_data, "mime_type": mime_type}
    except (binascii.Error, ValueError) as e:
        logger.warning("Base64解码失败: %s", e)
        return None


async def _auto_provision_openclaw_channel(
    request: Request,
    user_id: str,
    agent_id: str,
) -> None:
    """Fire-and-forget: 从 OpenClaw 请求自动发现并写入渠道配置.

    account_id 从请求注入的 Inbound Context 直接提取(请求自带, 非猜测),
    渠道配置按 (user, thread, agent) 物理隔离存储.
    已写入数据库后不再重复提取.
    """
    ctx = getattr(request.state, "openclaw_context", None)
    if ctx is None:
        return

    target = ctx.chat_id
    channel_key = ctx.channel
    if not target or not channel_key:
        return

    thread_id = getattr(request.state, "thread_id", None)
    if not thread_id:
        return

    try:
        from src.storage.service.user_channel_config_service import (
            get_user_channel_config_service,
        )

        config_service = await get_user_channel_config_service(
            user_id, thread_id, agent_id
        )
        existing = await config_service.get_config_for_channel("wechat")

        if existing and existing.get("openclaw_account"):
            return

        account_id = ctx.account_id

        if existing:
            existing["openclaw_account"] = account_id or ""
            await config_service.upsert_channel_config(
                channel_type="wechat",
                config=existing,
                is_default=True,
            )
            logger.info(
                "增量补充OpenClaw account: user=%s, account=%s",
                user_id,
                account_id,
            )
            return

        await config_service.upsert_channel_config(
            channel_type="wechat",
            config={
                "target": target,
                "openclaw_channel_key": channel_key,
                "openclaw_account": account_id or "",
            },
            is_default=True,
        )
        logger.info("✅ 自动发现OpenClaw渠道配置: user=%s, target=%s", user_id, target)

    except Exception as e:
        logger.warning("自动写入OpenClaw渠道配置失败(非阻塞): %s", e)


# ========== 路由处理函数 ==========


@router.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    request: Request,
    chat_request: ChatCompletionRequest,
) -> Response:
    """聊天完成API端点 - OpenAI标准JSON格式.

    支持格式:
    1. 纯文本: {"role": "user", "content": "你好"}
    2. 多模态: {"role": "user", "content": [
         {"type": "text", "text": "这是什么?"},
         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
       ]}

    Args:
        request: HTTP请求对象
        chat_request: 聊天完成请求(JSON格式)

    Returns:
        聊天完成响应(支持流式和非流式)

    """
    logger.info(
        f"收到聊天请求: {len(chat_request.messages)} 条消息 [{'流式' if chat_request.stream else '非流式'}]"
    )

    start_time = time.time()
    try:
        # 1. 提取用户消息(多模态内容)
        user_message = chat_request.messages[-1]
        user_input, image_datas = _extract_content_with_images(user_message.content)

        # 1.5 捕获前端透传历史(messages[:-1]), 供 simple 模式处理器透传给 LLM.
        # local 模式不读此字段(从自有 DB 重建历史), 透传对其透明.
        chat_messages = [
            m.model_dump(exclude_none=True) for m in chat_request.messages[:-1]
        ]

        # 2. 从认证中间件获取用户ID和线程ID
        user_id = getattr(request.state, "user_id", None)
        thread_id = getattr(request.state, "thread_id", None)
        timezone = getattr(request.state, "timezone", "Asia/Shanghai")

        # 3. 验证认证中间件是否正确设置了用户信息
        if not user_id or not thread_id:
            logger.error("❌ 认证中间件未正确设置用户信息")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication middleware error: missing user information",
            )

        # 4. 确保用户目录存在
        try:
            resolver = get_user_path_resolver()
            resolver.get_thread_base_path(user_id, thread_id)  # 自动创建用户目录
            logger.info("✅ 用户目录已确认: %s", user_id)
        except (PermissionError, OSError) as e:
            logger.error("❌ 创建用户目录失败 %s: %s", user_id, e)
            # 不阻止处理,但记录错误

        # 4.5 OpenClaw 渠道配置自动发现
        await _auto_provision_openclaw_channel(request, user_id, chat_request.model)

        # 5. 验证并获取Agent实例
        logger.info(f"准备获取Agent: {chat_request.model}")
        try:
            agent = await get_agent(chat_request.model)
            logger.info(f"Agent获取成功: {chat_request.model}")
        except Exception as e:
            logger.error(f"Agent获取失败: {chat_request.model}, 错误: {e}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent '{chat_request.model}' not found or failed to load",
            ) from e

        is_openclaw = getattr(request.state, "is_openclaw", False)

        llm_model_id = getattr(getattr(agent, "config", None), "model_id", "")
        model_meta = get_model(llm_model_id)
        is_multimodal = model_meta is not None and model_meta.supports_multimodal()

        if chat_request.stream:
            logger.info("启用流式响应模式")

            if is_openclaw:
                return StreamingResponse(
                    stream_openclaw_response(
                        agent=agent,
                        user_input=user_input,
                        user_id=user_id,
                        thread_id=thread_id,
                        model_id=chat_request.model,
                        image_datas=image_datas,
                        timezone=timezone,
                        is_multimodal=is_multimodal,
                        chat_messages=chat_messages,
                    ),
                    media_type="text/event-stream",
                )

            return StreamingResponse(
                locked_stream_chat_completion(
                    agent=agent,
                    user_input=user_input,
                    user_id=user_id,
                    thread_id=thread_id,
                    model_id=chat_request.model,
                    image_datas=image_datas,
                    timezone=timezone,
                    is_multimodal=is_multimodal,
                    chat_messages=chat_messages,
                ),
                media_type="text/event-stream",
            )
        logger.info("启用非流式响应模式")

        # 非流式: 通过消息队列保证顺序处理
        queue = SessionMessageQueue.get(user_id, thread_id, chat_request.model)
        response_future = await queue.submit(
            user_input=user_input,
            image_datas=image_datas or [],
            timezone=timezone,
            agent=agent,
            is_openclaw=is_openclaw,
            is_multimodal=is_multimodal,
            chat_messages=chat_messages,
        )
        response = await response_future
        if response is None:
            response = ""  # 被合并吸收
        processing_time = time.time() - start_time
        logger.info(
            f"消息处理完成: {chat_request.model}, 耗时 {processing_time:.2f}s",
        )

        # 8. 估算token使用情况
        try:
            token_estimator = TokenEstimator()
            input_tokens = token_estimator.estimate_tokens(user_input)
            response_tokens = token_estimator.estimate_tokens(response)
            total_tokens = input_tokens + response_tokens
        except Exception as e:
            logger.warning("⚠️ Token估算失败: %s", e)
            input_tokens = None
            response_tokens = None
            total_tokens = None

        # 9. 构建OpenAI格式的响应
        completion_id = f"chatcmpl-{int(time.time() * 1000)}"

        return ChatCompletionResponse(  # type: ignore[return-value]
            id=completion_id,
            created=int(time.time()),
            model=chat_request.model,
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response,
                    },
                    "finish_reason": "stop",
                },
            ],
            usage={
                "prompt_tokens": input_tokens,
                "completion_tokens": response_tokens,
                "total_tokens": total_tokens,
            },
        )

    except HTTPException:
        # 重新抛出HTTP异常
        raise
    except Exception as e:
        logger.error("❌ 聊天完成API未处理的异常: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during chat completion processing",
        ) from e


@router.get("/v1/models")
async def list_models() -> dict[str, Any]:
    """获取可用模型(Agent)列表 - 兼容OpenAI格式.

    Returns:
        模型列表,包含id和object字段

    """
    try:
        agents = await list_agents()
        models = [
            {
                "id": agent["id"],
                "object": "model",
                "created": int(time.time()),
                "owned_by": "personal-assistant",
            }
            for agent in agents
        ]

        logger.info(f"📋 返回 {len(models)} 个可用模型")
        return {
            "object": "list",
            "data": models,
        }
    except Exception as e:
        logger.error("❌ 获取模型列表失败: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve model list",
        ) from e


async def locked_stream_chat_completion(
    agent: Any,
    user_input: str,
    user_id: str,
    thread_id: str,
    model_id: str,
    image_datas: list[dict] | None = None,
    timezone: str = "Asia/Shanghai",
    *,
    is_multimodal: bool = False,
    chat_messages: list[dict] | None = None,
) -> AsyncIterator[str]:
    """标准流式聊天处理 — 通过消息队列保证顺序, 逐 token 流式输出.

    与 OpenClaw 路径不同: 无心跳, 无消息拆分, 纯 token 级 SSE 流.
    """
    queue = SessionMessageQueue.get(user_id, thread_id, model_id)
    completion_id = generate_completion_id()
    created = int(time.time())

    token_stream = await queue.submit_streaming(
        user_input=user_input,
        image_datas=image_datas or [],
        timezone=timezone,
        agent=agent,
        is_multimodal=is_multimodal,
        chat_messages=chat_messages,
    )

    has_content = False
    try:
        async for token in token_stream:
            has_content = True
            yield create_stream_chunk(
                completion_id=completion_id,
                created=created,
                model=model_id,
                content=token,
            )

        yield create_stream_final_chunk(
            completion_id=completion_id,
            created=created,
            model=model_id,
        )
    except Exception as e:
        logger.error("❌ 流式响应失败: %s", e)
        if has_content:
            yield create_stream_error_chunk(str(e), "stream_error")
        else:
            yield create_stream_final_chunk(
                completion_id=completion_id,
                created=created,
                model=model_id,
            )


# ========== OpenClaw 流式保活 ==========

HEARTBEAT_INTERVAL_SECONDS = 90


def _heartbeat_sse_chunk(payload_id: str, model_id: str) -> str:
    """生成单空格心跳 chunk.

    delta.content=' ' 确保触发 OpenClaw 的 text_delta 事件链路:
    processOpenAICompletionsStream -> onPartialReply -> markProgress,
    防止 stuck session abort (阈值 360s).

    finish_reason 必须为 null, 否则 OpenClaw 视为对话结束.
    """
    chunk = {
        "id": payload_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "delta": {"content": " "},
                "finish_reason": None,
            },
        ],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


async def stream_openclaw_response(
    agent: Any,
    user_input: str,
    user_id: str,
    thread_id: str,
    model_id: str,
    image_datas: list[dict] | None = None,
    timezone: str = "Asia/Shanghai",
    *,
    is_multimodal: bool = False,
    chat_messages: list[dict] | None = None,
) -> AsyncIterator[str]:
    """OpenClaw 专用流式响应: 立即心跳 + 消息队列保序.

    架构:
        - 心跳在入队后立即启动(关键改进:不等处理开始)
        - 通过 SessionMessageQueue 保证顺序处理 + 消息合并
        - 业务处理在队列的 _processor_loop 中执行

    Yields:
        SSE 格式字符串
    """
    queue = SessionMessageQueue.get(user_id, thread_id, model_id)
    completion_id = generate_completion_id()
    created = int(time.time())

    # 立即启动心跳(关键改进:不依赖任何锁/队列处理状态)
    async def heartbeat_loop(out: asyncio.Queue[str | None]) -> None:
        """周期性推送心跳标记到输出队列."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            await out.put("heartbeat")

    output_queue: asyncio.Queue[str | None] = asyncio.Queue()

    # 心跳先启动
    hb = asyncio.create_task(heartbeat_loop(output_queue))

    try:
        # 入队并获取 future
        response_future = await queue.submit(
            user_input=user_input,
            image_datas=image_datas or [],
            timezone=timezone,
            agent=agent,
            is_openclaw=True,
            is_multimodal=is_multimodal,
            chat_messages=chat_messages,
        )

        # 持续 yield 心跳,等待处理完成
        while not response_future.done():
            try:
                msg = await asyncio.wait_for(output_queue.get(), timeout=1.0)
                if msg == "heartbeat":
                    yield _heartbeat_sse_chunk(completion_id, model_id)
            except TimeoutError:
                continue

        # 处理完成,取结果
        result = response_future.result()

        if result is None:
            # 被合并吸收 → 静默关闭
            return

        # 正常响应
        if len(result) > 2000:
            from src.session.openclaw_message_splitter import (
                send_openclaw_followup,
                split_message,
            )

            parts = split_message(result)
            result = parts[0]
            # model_id 在 OpenClaw 场景即 agent 标识 (chat_request.model = agent_id,
            # 见 get_agent(chat_request.model)), 故第三参数语义为 agent_id,
            # 用于定位 agent 级 channel_config; 切勿按字面理解为模型 ID.
            if len(parts) > 1:
                spawn_background_task(
                    send_openclaw_followup(user_id, thread_id, model_id, parts[1:]),
                )

        yield create_stream_chunk(
            completion_id=completion_id,
            created=created,
            model=model_id,
            content=result,
        )
        yield create_stream_final_chunk(
            completion_id=completion_id,
            created=created,
            model=model_id,
        )
    except Exception as e:
        logger.error("❌ OpenClaw 流式响应失败: %s", e)
        yield create_stream_error_chunk(str(e), "openclaw_processing_error")
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
