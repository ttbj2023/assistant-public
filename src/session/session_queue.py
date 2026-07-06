"""Per-session 消息队列 — 替代 asyncio.Lock 实现顺序处理 + 消息合并.

设计目标:
1. 每个 user:thread:agent 组合维护一个队列, 保证消息顺序处理
2. 当队列中积累多条消息时自动合并为一次 agent 调用
3. 支持 streaming 模式: 逐 token 推送, 仍保持队列顺序保证
4. 被合并吸收的消息 SSE 纯静默关闭
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, ClassVar

from src.session.chat_helpers import (
    allocate_round_number,
    prepare_image_attachments,
)
from src.utils.message_formatting import build_file_links, build_media_lines

logger = logging.getLogger(__name__)


@dataclass
class QueuedMessage:
    """队列中的单条消息."""

    user_input: str
    image_datas: list[dict]
    timezone: str
    is_openclaw: bool = False
    streaming: bool = False
    is_multimodal: bool = False
    response_future: asyncio.Future[str | None] | None = None
    token_sink: asyncio.Queue[str | None] | None = None
    arrived_at: float = field(default_factory=time.time)
    chat_messages: list[dict] | None = None


class SessionMessageQueue:
    """Per user:thread:agent 消息队列.

    核心机制:
    - submit() 入队消息并返回 future, 调用方通过 future 等待响应
    - _processor_loop() 后台处理循环: 取消息 → 合并 → 调用 agent → 设置 future
    - 合并策略: 立即清空队列中所有已积累消息, 合并为一次 agent 调用
    - 响应路由: 合并批次只有最后一条消息的 future 收到响应, 其余收到 None
    """

    _instances: ClassVar[dict[str, SessionMessageQueue]] = {}

    def __init__(self, key: str) -> None:
        self._key = key
        self._pending: deque[QueuedMessage] = deque()
        self._event = asyncio.Event()
        self._processing = False
        self._processor: asyncio.Task[None] | None = None
        self._agent_ref: Any = None

    # ========== 公共接口 ==========

    @classmethod
    def get(cls, user_id: str, thread_id: str, agent_id: str) -> SessionMessageQueue:
        """获取或创建 session 队列实例."""
        key = f"{user_id}:{thread_id}:{agent_id}"
        if key not in cls._instances:
            cls._instances[key] = SessionMessageQueue(key)
        return cls._instances[key]

    async def submit(
        self,
        user_input: str,
        image_datas: list[dict],
        timezone: str,
        agent: Any,
        *,
        is_openclaw: bool = False,
        is_multimodal: bool = False,
        chat_messages: list[dict] | None = None,
    ) -> asyncio.Future[str | None]:
        """入队一条消息, 返回 future 用于等待响应.

        future 结果:
        - str: 正常响应内容
        - None: 该消息被合并吸收, SSE 应静默关闭
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._agent_ref = agent

        self._pending.append(
            QueuedMessage(
                user_input=user_input,
                image_datas=image_datas,
                timezone=timezone,
                is_openclaw=is_openclaw,
                is_multimodal=is_multimodal,
                response_future=future,
                arrived_at=time.time(),
                chat_messages=chat_messages,
            ),
        )

        logger.info(
            "📥 消息入队 [%s], 队列深度: %d, 处理中: %s",
            self._key,
            len(self._pending),
            self._processing,
        )

        self._event.set()
        self._ensure_processor()
        return future

    async def submit_streaming(
        self,
        user_input: str,
        image_datas: list[dict],
        timezone: str,
        agent: Any,
        *,
        is_multimodal: bool = False,
        chat_messages: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """入队一条消息, 返回 async iterator 逐 token 产出响应.

        iterator 行为:
        - 逐个 yield token 字符串
        - 迭代结束表示响应完成或被合并吸收 (无 token 产出)
        """
        sink: asyncio.Queue[str | None] = asyncio.Queue()
        self._agent_ref = agent

        self._pending.append(
            QueuedMessage(
                user_input=user_input,
                image_datas=image_datas,
                timezone=timezone,
                streaming=True,
                is_multimodal=is_multimodal,
                token_sink=sink,
                arrived_at=time.time(),
                chat_messages=chat_messages,
            ),
        )

        logger.info(
            "📥 消息入队(streaming) [%s], 队列深度: %d, 处理中: %s",
            self._key,
            len(self._pending),
            self._processing,
        )

        self._event.set()
        self._ensure_processor()
        return self._consume_sink(sink)

    @staticmethod
    async def _consume_sink(sink: asyncio.Queue[str | None]) -> AsyncIterator[str]:
        """从 token sink 读取, None 哨兵表示结束."""
        while True:
            token = await sink.get()
            if token is None:
                return
            yield token

    # ========== 处理循环 ==========

    def _ensure_processor(self) -> None:
        """确保 processor 正在运行."""
        if not self._processing:
            self._processing = True
            self._processor = asyncio.create_task(self._processor_loop())
            logger.debug("🔧 启动 processor: %s", self._key)

    async def _processor_loop(self) -> None:
        """处理循环: 等消息 → 清空合并 → 处理 → 检查积压."""
        try:
            while True:
                # 等待消息到达
                await self._event.wait()
                self._event.clear()

                if not self._pending:
                    continue

                # 立即清空所有已积累的消息
                batch = list(self._pending)
                self._pending.clear()

                if len(batch) == 1:
                    logger.info(
                        "🔄 处理单条消息 [%s], 等待时长: %.1fs",
                        self._key,
                        time.time() - batch[0].arrived_at,
                    )
                    await self._process_single(batch[0])
                else:
                    logger.info(
                        "🔀 合并处理 %d 条消息 [%s]",
                        len(batch),
                        self._key,
                    )
                    await self._process_merged(batch)

                # 检查是否还有积压(有的话继续循环)
                if not self._pending:
                    break
        except Exception:
            logger.exception("❌ processor_loop 异常 [%s]", self._key)
            while self._pending:
                msg = self._pending.popleft()
                self._signal_absorbed(msg)
        finally:
            self._processing = False
            logger.debug("🏁 processor 退出: %s", self._key)

    # ========== 处理策略 ==========

    async def _process_single(self, msg: QueuedMessage) -> None:
        """处理单条消息."""
        try:
            if msg.streaming:
                await self._execute_agent_streaming(
                    text=msg.user_input,
                    image_datas=msg.image_datas,
                    timezone=msg.timezone,
                    sink=msg.token_sink,
                    is_multimodal=msg.is_multimodal,
                    chat_messages=msg.chat_messages,
                )
            else:
                response, _ = await self._execute_agent(
                    text=msg.user_input,
                    image_datas=msg.image_datas,
                    timezone=msg.timezone,
                    is_openclaw=msg.is_openclaw,
                    is_multimodal=msg.is_multimodal,
                    chat_messages=msg.chat_messages,
                )
                if msg.response_future and not msg.response_future.done():
                    msg.response_future.set_result(response)
        except Exception as e:
            logger.exception("❌ 处理单条消息失败 [%s]", self._key)
            if msg.streaming and msg.token_sink:
                await msg.token_sink.put(None)
            elif msg.response_future and not msg.response_future.done():
                msg.response_future.set_exception(e)

    async def _process_merged(self, batch: list[QueuedMessage]) -> None:
        """合并处理多条消息."""
        try:
            texts = [m.user_input for m in batch]
            lines = [f"{i + 1}. {t}" for i, t in enumerate(texts)]
            merged_text = (
                "用户连续发送了以下消息:\n"
                + "\n".join(lines)
                + "\n\n请综合处理以上所有请求."
            )

            merged_images: list[dict] = []
            for m in batch:
                merged_images.extend(m.image_datas)

            timezone = batch[0].timezone

            logger.info(
                "🔀 合并 %d 条消息, 文本长度: %d, 图片数: %d",
                len(batch),
                len(merged_text),
                len(merged_images),
            )

            last = batch[-1]

            if last.streaming:
                await self._execute_agent_streaming(
                    text=merged_text,
                    image_datas=merged_images,
                    timezone=timezone,
                    sink=last.token_sink,
                    is_multimodal=last.is_multimodal,
                    chat_messages=last.chat_messages,
                )
            else:
                response, _ = await self._execute_agent(
                    text=merged_text,
                    image_datas=merged_images,
                    timezone=timezone,
                    is_openclaw=last.is_openclaw,
                    is_multimodal=last.is_multimodal,
                    chat_messages=last.chat_messages,
                )
                if last.response_future and not last.response_future.done():
                    last.response_future.set_result(response)

            for msg in batch[:-1]:
                self._signal_absorbed(msg)

        except Exception as e:
            logger.exception("❌ 合并处理失败 [%s]", self._key)
            for msg in batch:
                if msg.streaming and msg.token_sink:
                    await msg.token_sink.put(None)
                elif msg.response_future and not msg.response_future.done():
                    msg.response_future.set_exception(e)

    @staticmethod
    def _signal_absorbed(msg: QueuedMessage) -> None:
        """通知消息被合并吸收."""
        if msg.streaming and msg.token_sink:
            msg.token_sink.put_nowait(None)
        elif msg.response_future and not msg.response_future.done():
            msg.response_future.set_result(None)

    # ========== Agent 执行 ==========

    async def _execute_agent(
        self,
        text: str,
        image_datas: list[dict],
        timezone: str,
        *,
        is_openclaw: bool = False,
        is_multimodal: bool = False,
        chat_messages: list[dict] | None = None,
    ) -> tuple[str, list[dict]]:
        """分配轮次号 → 处理图片 → 调用 agent → 返回 (响应, exported_files)."""
        from src.core.context import (
            UserContext,
            get_user_context_or_none,
            reset_user_context,
            set_user_context,
        )

        parts = self._key.split(":")
        user_id, thread_id, agent_id = parts[0], parts[1], parts[2]
        agent = self._agent_ref

        # 1. 分配轮次号
        round_number = await allocate_round_number(user_id, thread_id, agent_id)
        logger.info(
            "📋 分配轮次号: %d [%s]",
            round_number,
            self._key,
        )

        # 2. 设置用户上下文, 确保附件视觉模型和后台任务也能归属到本轮.
        ctx_token = set_user_context(
            UserContext(
                user_id=user_id,
                thread_id=thread_id,
                agent_id=agent_id,
                request_id=f"chat-{uuid.uuid4().hex}",
                round_number=round_number,
                usage_source="main_chat",
                is_openclaw=is_openclaw,
            ),
        )

        exported_files: list[dict] = []
        try:
            # 3. 处理图片附件
            attachment_infos = await prepare_image_attachments(
                user_id=user_id,
                thread_id=thread_id,
                is_multimodal=is_multimodal,
                image_datas=image_datas,
                round_number=round_number,
            )

            # 4. 调用 agent
            response = await agent.process_message(
                message=text,
                user_id=user_id,
                thread_id=thread_id,
                model_id=agent_id,
                image_datas=image_datas if image_datas else None,
                attachment_infos=attachment_infos,
                timezone=timezone,
                round_number=round_number,
                chat_messages=chat_messages,
            )

            # 5. 收集 exported_files (文件下载链接)
            ctx_snap = get_user_context_or_none()
            if ctx_snap:
                exported_files = list(ctx_snap.exported_files)

            # 6. 拼接文件链接到响应
            if exported_files:
                if ctx_snap and ctx_snap.is_openclaw:
                    media_lines = build_media_lines(exported_files)
                    if media_lines:
                        response = f"{response}\n{media_lines}"
                else:
                    file_links = build_file_links(exported_files)
                    if file_links:
                        response = f"{response}{file_links}"

            return response, exported_files
        finally:
            reset_user_context(ctx_token)

    async def _execute_agent_streaming(
        self,
        text: str,
        image_datas: list[dict],
        timezone: str,
        sink: asyncio.Queue[str | None],
        *,
        is_multimodal: bool = False,
        chat_messages: list[dict] | None = None,
    ) -> None:
        """流式执行 agent, 将 token 推入 sink, 流结束后 finalize."""
        from src.core.context import (
            UserContext,
            get_user_context_or_none,
            reset_user_context,
            set_user_context,
        )
        from src.core.streaming import StreamContent

        parts = self._key.split(":")
        user_id, thread_id, agent_id = parts[0], parts[1], parts[2]
        agent = self._agent_ref

        round_number = await allocate_round_number(user_id, thread_id, agent_id)
        logger.info(
            "📋 分配轮次号: %d [%s] (streaming)",
            round_number,
            self._key,
        )

        ctx_token = set_user_context(
            UserContext(
                user_id=user_id,
                thread_id=thread_id,
                agent_id=agent_id,
                request_id=f"chat-{uuid.uuid4().hex}",
                round_number=round_number,
                usage_source="main_chat",
                is_openclaw=False,
            ),
        )

        full_response_parts: list[str] = []
        try:
            attachment_infos = await prepare_image_attachments(
                user_id=user_id,
                thread_id=thread_id,
                is_multimodal=is_multimodal,
                image_datas=image_datas,
                round_number=round_number,
            )

            async for item in agent.process_message_stream(
                message=text,
                user_id=user_id,
                thread_id=thread_id,
                model_id=agent_id,
                image_datas=image_datas if image_datas else None,
                attachment_infos=attachment_infos,
                timezone=timezone,
                round_number=round_number,
                chat_messages=chat_messages,
            ):
                if isinstance(item, StreamContent):
                    content = item.content
                    if not item.display_only:
                        full_response_parts.append(content)
                else:
                    content = item
                    full_response_parts.append(content)

                await sink.put(content)

            full_response = "".join(full_response_parts)
            if not full_response.strip():
                full_response = "返回响应为空"
                logger.warning("🔍 流式 Agent 返回空响应")

            await agent.finalize_conversation(
                user_input=text,
                response=full_response,
                user_id=user_id,
                thread_id=thread_id,
                model_id=agent_id,
                attachment_infos=attachment_infos,
                timezone=timezone,
                round_number=round_number,
            )
            logger.info("✅ 流式响应记忆存储完成: %s:%s", user_id, thread_id)

            ctx_snap = get_user_context_or_none()
            if ctx_snap and ctx_snap.exported_files:
                file_links = build_file_links(ctx_snap.exported_files)
                if file_links:
                    await sink.put(file_links)
        except Exception as e:
            logger.error("❌ 流式执行失败 [%s]: %s", self._key, e)
            raise
        finally:
            reset_user_context(ctx_token)
            await sink.put(None)
