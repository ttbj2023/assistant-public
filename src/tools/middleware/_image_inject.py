"""知识库图片直注中间件 - 多模态主模型短路 kb_read_image, 原图直注主对话.

主对话模型多模态时, kb_read_image 的按需读图调用无需视觉模型转述:
本中间件短路工具执行, 读原图转为 base64, 以
Command(文本 ToolMessage + 携带 image_url 块的 HumanMessage) 注入消息流
(image 放 HumanMessage 是 provider 安全形态, 各家多模态 API 均支持).

并行 kb_read_image 时, 直注 HumanMessage 会按 ToolNode 执行序穿插在
多个 ToolMessage 之间, 违反 "assistant 的 tool_calls 须由连续 ToolMessage
立即响应" 的 API 约束 (DeepSeek 直接 400); 因此模型调用前重排消息,
把直注 HumanMessage 移到全部 ToolMessage 之后.

非多模态 / ref 非法 / 图片缺失 / 图片超大 时透传 handler,
降级走工具内部视觉转述.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ToolCallRequest
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    ToolMessage,
)

from src.tools.external.kb_read_image_tool import (
    _MIME_BY_SUFFIX,
    parse_image_ref,
    resolve_corpus_root,
)

logger = logging.getLogger(__name__)

_TARGET_TOOL = "kb_read_image"

# 直注原图大小上限: 超大文件 base64 膨胀(约1.33倍)易触发请求体限制
_MAX_INJECT_BYTES = 5 * 1024 * 1024


def _model_supports_direct(llm_model: str | None) -> bool:
    """主对话模型是否支持多模态直注; 模型不在注册表时保守返回 False."""
    if not llm_model:
        return False
    from src.inference.llm.definitions.model_registry import get_model

    meta = get_model(llm_model)
    return meta is not None and meta.supports_multimodal()


def _tool_responses_first(messages: list[AnyMessage]) -> list[AnyMessage]:
    """重排消息: assistant 多 tool_calls 的 ToolMessage 响应连续前置.

    仅处理响应齐全的块: 块内非 ToolMessage(如直注 HumanMessage)移到该块
    全部 ToolMessage 之后, ToolMessage 与被移消息各自保持相对顺序;
    响应不齐全或本就连续的块原样保留(幂等).
    """
    result = list(messages)
    i = 0
    while i < len(result):
        msg = result[i]
        calls = getattr(msg, "tool_calls", None)
        if not isinstance(msg, AIMessage) or not calls:
            i += 1
            continue
        needed = {tc.get("id") for tc in calls}
        tools: list[AnyMessage] = []
        others: list[AnyMessage] = []
        j = i + 1
        completed = False
        while j < len(result):
            m = result[j]
            if isinstance(m, AIMessage):
                break
            if isinstance(m, ToolMessage):
                tools.append(m)
                if m.tool_call_id in needed:
                    needed.discard(m.tool_call_id)
                    if not needed:
                        completed = True
            else:
                others.append(m)
            j += 1
            if completed:
                break
        if completed and others:
            result[i + 1 : j] = tools + others
        i += 1
    return result


class KbImageInjectMiddleware(AgentMiddleware):
    """多模态主模型下 kb_read_image 直注原图中间件."""

    def __init__(self, llm_model: str | None) -> None:
        self._direct = _model_supports_direct(llm_model)
        logger.info(
            "KbImageInjectMiddleware初始化: 直注=%s (model=%s)",
            self._direct,
            llm_model,
        )

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Any],
    ) -> Any:
        """模型调用前重排消息, 保证直注 HumanMessage 不打断 ToolMessage 连续性."""
        if not self._direct:
            return await handler(request)
        reordered = _tool_responses_first(request.messages)
        if reordered is request.messages or reordered == request.messages:
            return await handler(request)
        moved = sum(
            1 for i, j in zip(request.messages, reordered, strict=False) if i is not j
        )
        logger.info(
            "🖼️ 直注重排: %d 个消息位置调整, ToolMessage 响应块连续化 (provider 约束)",
            moved,
        )
        return await handler(request.override(messages=reordered))

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        tool_call = request.tool_call
        if tool_call.get("name", "") != _TARGET_TOOL or not self._direct:
            return await handler(request)

        args = tool_call.get("args", {}) or {}
        image_ref = str(args.get("image_ref", ""))
        injection = await self._load_image(image_ref)
        if injection is None:
            return await handler(request)

        image_path, image_bytes = injection
        suffix = image_path.suffix.lower()
        mime = _MIME_BY_SUFFIX.get(suffix, "image/jpeg")
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        prompt = str(args.get("prompt", ""))

        tool_message = ToolMessage(
            content=(
                f"知识库图片 {image_ref} 原图已直接注入对话, "
                f"请依据图片本体回答读图要求, 无需转述."
            ),
            tool_call_id=str(tool_call.get("id", "")),
            name=_TARGET_TOOL,
        )
        human_message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": f"[知识库图片 {image_ref}]\n读图要求: {prompt}",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
            ],
        )
        logger.info(
            "🖼️ 知识库图片直注主对话: %s (%d bytes)", image_ref, len(image_bytes)
        )
        from langgraph.types import Command

        return Command(update={"messages": [tool_message, human_message]})

    @staticmethod
    async def _load_image(image_ref: str) -> tuple[Path, bytes] | None:
        """解析 ref 并读取原图; 非法/缺失/超大返回 None (降级)."""
        try:
            kb_name, rel_path = parse_image_ref(image_ref)
        except ValueError as e:
            logger.info("直注跳过(%s), 降级视觉转述: %s", e, image_ref)
            return None

        image_path = resolve_corpus_root(kb_name) / rel_path
        if not image_path.is_file():
            logger.info("直注跳过(图片不存在), 降级视觉转述: %s", image_ref)
            return None

        import asyncio

        image_bytes = await asyncio.to_thread(image_path.read_bytes)
        if len(image_bytes) > _MAX_INJECT_BYTES:
            logger.info(
                "直注跳过(图片 %.1fMB 超上限), 降级视觉转述: %s",
                len(image_bytes) / 1024 / 1024,
                image_ref,
            )
            return None
        return image_path, image_bytes


__all__ = ["KbImageInjectMiddleware"]
