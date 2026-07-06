"""记忆检索工具 (search_memories) Agent 工具循环 E2E 测试.

独特价值 (集成/单元测试无法覆盖):
- 集成测试 mock 在 Service 层, 跑不到 create_agent 的真实 AgentExecutor 工具循环
- 单元测试 mock 函数级, 无法验证 DualStageRetrievalService 作为 Agent 工具被 LLM 触发
  后, 检索结果经 ToolMessage 反馈进下一轮 LLM 推理的完整链路
- 本测试通过 E2EMockLLM 注入 search_memories tool_call, 触发真实 DualStageRetrievalService
  (真实 SQLite + ChromaDB) 执行, 验证检索结果反馈
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
class TestMemoryRetrievalToolE2E:
    """search_memories 工具循环 E2E 测试."""

    async def test_e2e_search_memories_runs_in_agent_loop(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
        e2e_db_reader,
    ):
        """LLM 触发 search_memories → 真实 DualStageRetrievalService → 结果反馈进下一轮 LLM.

        独特价值: 验证真实 AgentExecutor 工具循环中, search_memories (常驻工具) 被调用后
                  真实双路检索服务执行 SQL+向量检索, 结果经 ToolMessage 注入第 2 轮 LLM 输入.
        """
        # 第 1 轮: seed 一条含独特关键词的历史 (供后续检索命中)
        E2EMockLLM.set_script([
            AIMessage(content="好的，已记录。", tool_calls=[]),
        ])
        r1 = await e2e_client.post(
            "/v1/chat/completions",
            json=_chat_request(
                "我负责的项目代号是猎鹰计划，下周要评审", e2e_test_thread_id
            ),
            headers={"Authorization": f"Bearer {e2e_api_key}"},
        )
        assert r1.status_code == 200

        # 确认历史已落库
        convs = e2e_db_reader.read_conversations(e2e_test_thread_id)
        assert len(convs) >= 1, "seed 轮应落库供检索"

        # 第 2 轮: 注入 search_memories tool_call，再给最终回答
        E2EMockLLM.set_script([
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_memories",
                        "args": {"query": "猎鹰计划"},
                        "id": "call_search_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="根据记忆，你负责猎鹰计划项目。", tool_calls=[]),
        ])
        r2 = await e2e_client.post(
            "/v1/chat/completions",
            json=_chat_request("帮我查一下我的项目信息", e2e_test_thread_id),
            headers={"Authorization": f"Bearer {e2e_api_key}"},
        )

        assert r2.status_code == 200
        data = r2.json()
        # 第 2 条脚本 (最终回答) 被返回，证明工具循环跑了 2 次 (tool_call → final)
        assert "猎鹰" in data["choices"][0]["message"]["content"]

        # 第 2 轮 ainvoke 的输入应包含 search_memories 的 ToolMessage 反馈
        # (真实 DualStageRetrievalService 执行后产出的检索结果 JSON)
        last_input_text = str(E2EMockLLM.get_last_input())
        assert "猎鹰" in last_input_text, (
            "检索结果应反馈进第 2 轮 LLM 输入 (ToolMessage 含命中历史)"
        )
