"""streaming 流式响应工具函数测试."""

from __future__ import annotations

from unittest.mock import patch

from src.core.streaming import (
    StreamChunk,
    create_stream_chunk,
    create_stream_error_chunk,
    create_stream_final_chunk,
    format_sse_chunk,
    generate_completion_id,
)


class TestGenerateCompletionId:
    def test_id_has_prefix(self):
        cid = generate_completion_id()
        assert cid.startswith("chatcmpl-")

    def test_ids_unique_even_same_millisecond(self):
        # 同一毫秒内多次调用也应唯一 (防碰撞)
        with patch("src.core.streaming.time.time", return_value=1_700_000_000.000):
            ids = [generate_completion_id() for _ in range(500)]
        assert len(set(ids)) == len(ids)


class TestStreamChunk:
    def test_serializes_content(self):
        chunk = StreamChunk(
            id="cmpl-1",
            created=1,
            model="m",
            choices=[{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
        )
        data = chunk.model_dump_json()
        assert "hi" in data

    def test_format_sse_chunk_wraps_data(self):
        chunk = StreamChunk(
            id="cmpl-1",
            created=1,
            model="m",
            choices=[{"index": 0, "delta": {}, "finish_reason": None}],
        )
        sse = format_sse_chunk(chunk)
        assert sse.startswith("data: ")
        assert sse.endswith("\n\n")


class TestCreateStreamChunk:
    def test_content_delta(self):
        sse = create_stream_chunk("cmpl-1", 1, "m", content="hello")
        assert "hello" in sse
        assert sse.startswith("data: ")

    def test_finish_reason(self):
        sse = create_stream_chunk("cmpl-1", 1, "m", finish_reason="stop")
        assert '"finish_reason":"stop"' in sse


class TestFinalAndErrorChunk:
    def test_final_chunk_has_done_marker(self):
        sse = create_stream_final_chunk("cmpl-1", 1, "m")
        assert sse.endswith("data: [DONE]\n\n")

    def test_error_chunk(self):
        sse = create_stream_error_chunk("boom")
        assert '"message": "boom"' in sse
        assert sse.startswith("data: ")
