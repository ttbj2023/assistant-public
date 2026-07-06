"""SSE 流式响应 E2E 测试.

独特价值: 验证真实 FastAPI + ASGI 管线的 SSE HTTP 传输层稳定性,
聚焦并发处理与长输入. chunk 结构/wire format 由单元测试覆盖
(tests/unit/core/test_streaming.py).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

from tests.e2e.mock_llm import E2EMockLLM


def _stream_request(content: str, thread_id: str) -> dict:
    return {
        "model": "personal-assistant",
        "messages": [{"role": "user", "content": content}],
        "stream": True,
        "user": thread_id,
    }


def _parse_sse_chunks(text: str) -> list[dict]:
    """解析 SSE 文本流为 chunk 列表."""
    chunks = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]":
                continue
            try:
                chunks.append(json.loads(data))
            except json.JSONDecodeError:
                pass
    return chunks


@pytest.mark.e2e
class TestStreamingE2E:
    """SSE 流式 HTTP 传输层 E2E 测试 (并发/长输入). chunk 结构由单元测试覆盖."""

    async def test_streaming_concurrent_clients(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
    ):
        """验证 3 个并发流式请求的 ASGI 并发处理.

        独特价值: 仅 E2E 能验证真实 HTTP 并发; 集成/单元测试无并发语义.
        """
        E2EMockLLM.set_script([
            AIMessage(content="并发响应1", tool_calls=[]),
            AIMessage(content="并发响应2", tool_calls=[]),
            AIMessage(content="并发响应3", tool_calls=[]),
        ])

        async def _one_request(idx: int) -> int:
            r = await e2e_client.post(
                "/v1/chat/completions",
                json=_stream_request(f"并发测试{idx}", e2e_test_thread_id),
                headers={"Authorization": f"Bearer {e2e_api_key}"},
            )
            return r.status_code

        codes = await asyncio.gather(*[_one_request(i) for i in range(3)])
        assert all(c == 200 for c in codes), f"并发请求状态码: {codes}"

    async def test_streaming_long_input(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
    ):
        """验证长输入 (2000字符) 的流式处理稳定性.

        独特价值: 验证大 payload 的 HTTP 传输 + 流式输出不中断.
        """
        E2EMockLLM.set_script([
            AIMessage(content="长输入处理完成", tool_calls=[]),
        ])

        long_text = "测试" * 1000
        response = await e2e_client.post(
            "/v1/chat/completions",
            json=_stream_request(long_text, e2e_test_thread_id),
            headers={"Authorization": f"Bearer {e2e_api_key}"},
        )

        assert response.status_code == 200
        chunks = _parse_sse_chunks(response.text)
        assert len(chunks) >= 1
