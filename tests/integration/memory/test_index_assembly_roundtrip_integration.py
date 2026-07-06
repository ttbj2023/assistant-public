"""对话索引生成→组装回读闭环集成测试.

验证 ConversationService 存储的索引元数据可被 MemoryAssembler 在预算截断场景下
读回并格式化为 <conversation_index> 伪对话轮,
补充单元测试中索引生成与组装读取各自为战的缺口.

注意: 本测试通过 ConversationService.create_conversation(..., metadata=...) 直接写入索引,
绕过 ConversationMemoryCore 内部的并行存储竞争, 聚焦索引存储→组装的端到端闭环.

测试策略: 灰盒 - 真实 ConversationService / MemoryAssembler / SQLite,
不涉及任何外部 LLM 调用.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agent.memory.local_memory.assembler import MemoryAssembler
from src.config.agent_config import AgentConfig, AgentMemoryConfig
from src.storage.dao import async_database_manager as adm
from src.storage.service.service_factory import (
    clear_vector_cache,
    create_conversation_service,
)

_AGENT_ID = "test-agent"


@pytest.fixture(autouse=True)
def _reset_db_and_service_state() -> Iterator[None]:
    """重置 DB 全局状态 + Service 缓存 + 记忆缓存, 避免跨事件循环/跨测试污染."""
    from src.agent.memory.local_memory.cache import reset_global_cache

    adm._db_cache_lock = asyncio.Lock()
    adm._db_manager_cache.clear()
    clear_vector_cache()
    reset_global_cache()
    yield
    adm._db_cache_lock = asyncio.Lock()
    adm._db_manager_cache.clear()
    clear_vector_cache()
    reset_global_cache()


def _make_assembler(user_id: str, thread_id: str) -> MemoryAssembler:
    """构造真实 MemoryAssembler 实例."""
    return MemoryAssembler(
        agent_id=_AGENT_ID,
        agent_config=AgentConfig(
            agent_id=_AGENT_ID,
            memory=AgentMemoryConfig(include_todo_in_context=False),
        ),
        user_id=user_id,
        thread_id=thread_id,
    )


async def _seed_conversations(
    user_id: str,
    thread_id: str,
    count: int,
    *,
    start_round: int = 1,
    msg_len: int = 40,
    metadata: dict | None = None,
) -> None:
    """预置 count 轮真实对话 (从 start_round 开始, 每轮 user/assistant 各 msg_len 字符)."""
    conv_service = await create_conversation_service(
        user_id, thread_id, agent_id=_AGENT_ID
    )
    pad = "x" * msg_len
    for offset in range(count):
        rn = start_round + offset
        await conv_service.create_conversation(
            user_message=f"第{rn}轮用户消息{pad}",
            assistant_response=f"第{rn}轮助手回复{pad}",
            user_id=user_id,
            thread_id=thread_id,
            agent_id=_AGENT_ID,
            round_number=rn,
            metadata=metadata,
        )


@pytest.mark.integration
class TestIndexAssemblyRoundtripIntegration:
    """对话索引生成→组装回读闭环集成测试."""

    @pytest.mark.asyncio
    async def test_generated_index_appears_in_assembled_context(
        self,
        test_user,
        test_thread_id,
    ):
        """多轮对话 → 写入带索引元数据 → assembler 读回含索引内容.

        通过小预算触发索引区伪对话轮, 验证 <conversation_index> 含 summary.
        """
        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        await conv_service.create_conversation(
            user_message="讨论Python异步编程",
            assistant_response="asyncio 是 Python 的异步 IO 库",
            user_id=test_user,
            thread_id=test_thread_id,
            agent_id=_AGENT_ID,
            round_number=1,
            metadata={
                "summary": "Python",
                "topic": "Python",
                "keywords": "asyncio,Python",
                "title": "异步编程讨论",
            },
        )
        await _seed_conversations(test_user, test_thread_id, count=1, start_round=2, msg_len=200)

        stored = await conv_service.get_conversation_by_round(
            test_user, test_thread_id, 1
        )
        assert stored is not None
        assert stored.summary == "Python"
        assert stored.topic == "Python"

        assembler = _make_assembler(test_user, test_thread_id)
        ctx = await assembler.assemble_memory_context(
            test_user, test_thread_id, total_budget=430
        )

        index_msgs = [
            m
            for m in ctx.history_messages
            if isinstance(m, AIMessage) and "<conversation_index>" in str(m.content)
        ]
        assert index_msgs, "应生成索引区伪对话轮"
        assert "Python" in str(index_msgs[0].content)

    @pytest.mark.asyncio
    async def test_budget_truncation_with_index_fallback(
        self,
        test_user,
        test_thread_id,
    ):
        """20 轮长对话 + total_budget=2000 → 主历史被截断, 索引区补全早期上下文.

        验证: 主历史消息数 < 40 (即 < 20 轮); history 含 [过往对话回顾] 伪对话轮.
        """
        await _seed_conversations(test_user, test_thread_id, count=20, start_round=1, msg_len=100)

        assembler = _make_assembler(test_user, test_thread_id)
        ctx = await assembler.assemble_memory_context(
            test_user, test_thread_id, total_budget=2000
        )

        real_history_msgs = [
            m for m in ctx.history_messages if m.content != "[过往对话回顾]"
        ]
        assert len(real_history_msgs) < 40, "主历史应被预算截断 (< 20 轮)"

        has_index_pseudo = any(
            isinstance(m, HumanMessage) and m.content == "[过往对话回顾]"
            for m in ctx.history_messages
        )
        assert has_index_pseudo, "主历史未覆盖第 1 轮时应生成索引区伪对话轮"

        index_msg = next(
            m
            for m in ctx.history_messages
            if isinstance(m, AIMessage) and "<conversation_index>" in str(m.content)
        )
        assert "</conversation_index>" in str(index_msg.content)

    @pytest.mark.asyncio
    async def test_incremental_index_updates(
        self,
        test_user,
        test_thread_id,
    ):
        """round 1-5 各写入不同 summary → assembler 读回包含多轮索引内容.

        验证索引按轮次独立存储, 增量更新不丢失.
        """
        summaries = [
            "第一轮话题",
            "第二轮话题",
            "第三轮话题",
            "第四轮话题",
            "第五轮话题",
        ]
        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        for rn, summary in enumerate(summaries, start=1):
            await conv_service.create_conversation(
                user_message=f"第{rn}轮消息",
                assistant_response="收到",
                user_id=test_user,
                thread_id=test_thread_id,
                agent_id=_AGENT_ID,
                round_number=rn,
                metadata={
                    "summary": summary,
                    "topic": "测试",
                    "keywords": "测试",
                    "title": f"第{rn}轮",
                },
            )

        for rn, expected in enumerate(summaries, start=1):
            stored = await conv_service.get_conversation_by_round(
                test_user, test_thread_id, rn
            )
            assert stored is not None
            assert stored.summary == expected

        # 预置足够多轮普通对话, 使主历史被截断从而触发索引区
        await _seed_conversations(test_user, test_thread_id, count=10, start_round=6, msg_len=100)

        assembler = _make_assembler(test_user, test_thread_id)
        ctx = await assembler.assemble_memory_context(
            test_user, test_thread_id, total_budget=1200
        )

        index_msg = next(
            m
            for m in ctx.history_messages
            if isinstance(m, AIMessage) and "<conversation_index>" in str(m.content)
        )
        index_content = str(index_msg.content)
        # 索引区包含多轮摘要, 至少应包含最后几轮
        assert "第五轮话题" in index_content
        assert "第四轮话题" in index_content or "第三轮话题" in index_content
