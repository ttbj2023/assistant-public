"""Agent 管线 E2E 测试 — 核心独特价值.

验证集成/单元测试无法覆盖的完整链路:
- HTTP → Agent → LLM(tool_calls) → 工具执行 → DB 持久化 → HTTP 响应
- 多轮 HTTP 请求间服务端会话历史持久化 + prompt 组装

独特价值 (为什么集成/单元测试无法覆盖):
- 集成测试 mock 在 Service/Processor 层, 无法验证 create_agent 的工具循环
- 单元测试 mock 在函数级, 无法验证 HTTP 边界 + FastAPI 中间件链
- 本测试通过 E2EMockLLM 注入 tool_calls, 触发真实 Agent 工具执行
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from tests.e2e.mock_llm import E2EMockLLM


def _chat_request(
    content: str,
    thread_id: str,
    model: str = "personal-assistant",
    stream: bool = False,
) -> dict:
    """构造 OpenAI 兼容请求体."""
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "stream": stream,
        "user": thread_id,
    }


@pytest.mark.e2e
class TestAgentPipelineE2E:
    """Agent 工具循环 + 多轮持久化 E2E 测试."""

    async def test_agent_tool_loop_executes_and_persists(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
        e2e_db_reader,
    ):
        """验证完整 Agent 工具循环: HTTP → LLM(tool_calls) → create_todo → DB.

        独特价值: 集成测试 mock 在 Service 层无法验证 create_agent 工具循环;
                  本测试通过 E2EMockLLM 注入 tool_calls 触发真实工具执行,
                  并灰盒读取 todo.db 确认副作用.
        """
        E2EMockLLM.set_script([
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_todo",
                        "args": {"title": "买牛奶", "priority": "high"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="已为你创建待办：买牛奶（高优先级）。",
                tool_calls=[],
            ),
        ])

        response = await e2e_client.post(
            "/v1/chat/completions",
            json=_chat_request("帮我建一个待办：买牛奶，高优先级", e2e_test_thread_id),
            headers={"Authorization": f"Bearer {e2e_api_key}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["choices"][0]["message"]["content"]

        todos = e2e_db_reader.read_todos(e2e_test_thread_id)
        assert len(todos) >= 1, "create_todo 应通过 Agent 循环写入 DB"
        assert any("买牛奶" in t.get("title", "") for t in todos)

    async def test_conversation_history_assembled_across_requests(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
    ):
        """验证服务端会话历史持久化 + prompt 组装.

        独特价值: 集成测试无 HTTP 边界, 无法验证跨请求的服务端持久化;
                  本测试通过 E2EMockLLM.get_last_input() 捕获第 2 轮 LLM 输入,
                  验证第 1 轮对话历史被正确组装进 prompt.
        """
        E2EMockLLM.set_script([
            AIMessage(content="你好张三，很高兴认识你。", tool_calls=[]),
        ])
        r1 = await e2e_client.post(
            "/v1/chat/completions",
            json=_chat_request("你好，我叫张三，住在杭州", e2e_test_thread_id),
            headers={"Authorization": f"Bearer {e2e_api_key}"},
        )
        assert r1.status_code == 200

        E2EMockLLM.set_script([
            AIMessage(content="你叫张三，住在杭州。", tool_calls=[]),
        ])
        r2 = await e2e_client.post(
            "/v1/chat/completions",
            json=_chat_request("我叫什么名字？住在哪里？", e2e_test_thread_id),
            headers={"Authorization": f"Bearer {e2e_api_key}"},
        )
        assert r2.status_code == 200

        last_input = E2EMockLLM.get_last_input()
        input_text = str(last_input)
        assert "张三" in input_text, "第 2 轮 prompt 应包含第 1 轮对话历史"
        assert "杭州" in input_text, "第 2 轮 prompt 应包含第 1 轮对话历史"
