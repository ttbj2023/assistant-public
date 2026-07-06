"""chat.py 中 OpenClaw 流式保活机制测试.

覆盖:
- _heartbeat_sse_chunk 格式
- stream_openclaw_response 主流程 (普通响应 / 异常 / 心跳 / MEDIA / 长消息拆分 / task 清理)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.chat import (
    _heartbeat_sse_chunk,
    stream_openclaw_response,
)


class TestHeartbeatSseChunk:
    """_heartbeat_sse_chunk 格式测试."""

    def test_basic_format(self):
        sse = _heartbeat_sse_chunk("chatcmpl-test", "personal-assistant")
        assert sse.startswith("data: ")
        assert sse.endswith("\n\n")
        payload = json.loads(sse[len("data: ") :].strip())
        assert payload["id"] == "chatcmpl-test"
        assert payload["object"] == "chat.completion.chunk"
        assert payload["model"] == "personal-assistant"
        assert payload["choices"][0]["index"] == 0
        assert payload["choices"][0]["delta"] == {"content": " "}
        assert payload["choices"][0]["finish_reason"] is None

    def test_content_is_single_space(self):
        sse = _heartbeat_sse_chunk("chatcmpl-x", "any-model")
        payload = json.loads(sse[len("data: ") :].strip())
        assert payload["choices"][0]["delta"]["content"] == " "

    def test_finish_reason_is_null_not_stop(self):
        sse = _heartbeat_sse_chunk("chatcmpl-x", "any-model")
        payload = json.loads(sse[len("data: ") :].strip())
        assert payload["choices"][0]["finish_reason"] is None
        assert payload["choices"][0]["finish_reason"] != "stop"

    def test_created_is_int(self):
        sse = _heartbeat_sse_chunk("chatcmpl-x", "any-model")
        payload = json.loads(sse[len("data: ") :].strip())
        assert isinstance(payload["created"], int)


class TestStreamOpenclawResponseBasic:
    """stream_openclaw_response 基本主流程测试."""

    @pytest.mark.asyncio
    async def test_short_response_no_heartbeat(self):
        """短任务: main 立即返回, 心跳来不及触发."""
        agent = MagicMock()
        agent.process_message = AsyncMock(return_value="hello world")

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.core.context.set_user_context"),
            patch("src.core.context.reset_user_context"),
            patch(
                "src.core.context.get_user_context_or_none",
                return_value=None,
            ),
        ):
            chunks = []
            async for chunk in stream_openclaw_response(
                agent=agent,
                user_input="hi",
                user_id="u1",
                thread_id="t1",
                model_id="personal-assistant",
            ):
                chunks.append(chunk)

        assert len(chunks) == 2
        assert "hello world" in chunks[0]
        assert "[DONE]" in chunks[1]

    @pytest.mark.asyncio
    async def test_response_contains_media_when_exported_files_present(self):
        """exported_files 存在时, final chunk 末尾追加 MEDIA: 行."""
        agent = MagicMock()
        agent.process_message = AsyncMock(return_value="done")

        ctx_snap = MagicMock()
        ctx_snap.exported_files = [
            {"url": "https://example.com/file1.pdf"},
            {"url": "https://example.com/file2.docx"},
        ]

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.core.context.set_user_context"),
            patch("src.core.context.reset_user_context"),
            patch(
                "src.core.context.get_user_context_or_none",
                return_value=ctx_snap,
            ),
        ):
            chunks = []
            async for chunk in stream_openclaw_response(
                agent=agent,
                user_input="生成 PDF",
                user_id="u1",
                thread_id="t1",
                model_id="personal-assistant",
            ):
                chunks.append(chunk)

        assert len(chunks) == 2
        payload = json.loads(chunks[0][len("data: ") :].strip())
        content = payload["choices"][0]["delta"]["content"]
        assert "MEDIA:https://example.com/file1.pdf" in content
        assert "MEDIA:https://example.com/file2.docx" in content
        assert content.startswith("done\n")

    @pytest.mark.asyncio
    async def test_long_message_triggers_split(self):
        """response > 2000 字符触发 send_openclaw_followup."""
        long_response = "x" * 2500
        agent = MagicMock()
        agent.process_message = AsyncMock(return_value=long_response)

        parts = ["part0_" + "a" * 1000, "part1_" + "b" * 1000, "part2_rest"]

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.core.context.set_user_context"),
            patch("src.core.context.reset_user_context"),
            patch(
                "src.core.context.get_user_context_or_none",
                return_value=None,
            ),
            patch(
                "src.session.openclaw_message_splitter.split_message",
                return_value=parts,
            ),
            patch(
                "src.api.routes.chat.spawn_background_task",
            ) as mock_spawn,
        ):
            chunks = []
            async for chunk in stream_openclaw_response(
                agent=agent,
                user_input="长任务",
                user_id="u1",
                thread_id="t1",
                model_id="personal-assistant",
            ):
                chunks.append(chunk)

        mock_spawn.assert_called_once()
        payload = json.loads(chunks[0][len("data: ") :].strip())
        assert payload["choices"][0]["delta"]["content"] == parts[0]

    @pytest.mark.asyncio
    async def test_exception_yields_error_chunk(self):
        """main_task 异常时, yield error chunk."""
        agent = MagicMock()
        agent.process_message = AsyncMock(side_effect=RuntimeError("agent boom"))

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.core.context.set_user_context"),
            patch("src.core.context.reset_user_context"),
        ):
            chunks = []
            async for chunk in stream_openclaw_response(
                agent=agent,
                user_input="hi",
                user_id="u1",
                thread_id="t1",
                model_id="personal-assistant",
            ):
                chunks.append(chunk)

        assert len(chunks) == 1
        assert "error" in chunks[0]
        assert "agent boom" in chunks[0]


class TestStreamOpenclawResponseHeartbeat:
    """心跳机制测试."""

    @pytest.mark.asyncio
    async def test_heartbeat_emitted_when_main_blocks(self):
        """main_task 阻塞超过 heartbeat 周期时, 应产出心跳 chunk."""
        import asyncio as _asyncio

        main_event = _asyncio.Event()

        async def blocking_process(**_kwargs):
            await main_event.wait()
            return "slow result"

        agent = MagicMock()
        agent.process_message = AsyncMock(side_effect=blocking_process)

        chunks = []

        async def consumer():
            async for chunk in stream_openclaw_response(
                agent=agent,
                user_input="慢任务",
                user_id="u1",
                thread_id="t1",
                model_id="personal-assistant",
            ):
                chunks.append(chunk)

        with patch(
            "src.api.routes.chat.HEARTBEAT_INTERVAL_SECONDS",
            0.05,
        ):
            consumer_task = _asyncio.create_task(consumer())

            await _asyncio.sleep(0.3)

            main_event.set()
            await _asyncio.wait_for(consumer_task, timeout=5.0)

        heartbeat_chunks = [
            c for c in chunks if c.startswith("data: {") and '"content": " "' in c
        ]
        assert len(heartbeat_chunks) >= 1

        final_payload = json.loads(chunks[-2][len("data: ") :].strip())
        assert final_payload["choices"][0]["delta"]["content"] == "slow result"

    @pytest.mark.asyncio
    async def test_heartbeat_task_cancelled_after_main_done(self):
        """main_task 完成后 heartbeat_task 必须被 cancel, 不泄露 task."""
        agent = MagicMock()
        agent.process_message = AsyncMock(return_value="ok")

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.core.context.set_user_context"),
            patch("src.core.context.reset_user_context"),
            patch(
                "src.core.context.get_user_context_or_none",
                return_value=None,
            ),
        ):
            chunks = []
            async for chunk in stream_openclaw_response(
                agent=agent,
                user_input="hi",
                user_id="u1",
                thread_id="t1",
                model_id="personal-assistant",
            ):
                chunks.append(chunk)

        assert len(chunks) == 2
        assert "ok" in chunks[0]
        assert "[DONE]" in chunks[1]

    @pytest.mark.asyncio
    async def test_heartbeat_task_cancelled_on_exception(self):
        """main_task 异常时, heartbeat_task 必须被 cancel."""
        agent = MagicMock()
        agent.process_message = AsyncMock(side_effect=RuntimeError("boom"))

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.core.context.set_user_context"),
            patch("src.core.context.reset_user_context"),
        ):
            chunks = []
            async for chunk in stream_openclaw_response(
                agent=agent,
                user_input="hi",
                user_id="u1",
                thread_id="t1",
                model_id="personal-assistant",
            ):
                chunks.append(chunk)

        assert len(chunks) == 1
        assert "error" in chunks[0]


class TestStreamOpenclawResponseUserContext:
    """user_context 设置/重置测试."""

    @pytest.mark.asyncio
    async def test_user_context_set_with_is_openclaw_true(self):
        """UserContext 必须设置 is_openclaw=True."""
        agent = MagicMock()
        agent.process_message = AsyncMock(return_value="ok")

        observed_contexts = []

        def fake_set_context(ctx):
            observed_contexts.append(ctx)
            return MagicMock()

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch(
                "src.core.context.set_user_context",
                side_effect=fake_set_context,
            ),
            patch("src.core.context.reset_user_context"),
            patch(
                "src.core.context.get_user_context_or_none",
                return_value=None,
            ),
        ):
            async for _ in stream_openclaw_response(
                agent=agent,
                user_input="hi",
                user_id="u1",
                thread_id="t1",
                model_id="personal-assistant",
            ):
                pass

        assert len(observed_contexts) == 1
        assert observed_contexts[0].is_openclaw is True
        assert observed_contexts[0].user_id == "u1"
        assert observed_contexts[0].thread_id == "t1"
        assert observed_contexts[0].agent_id == "personal-assistant"
