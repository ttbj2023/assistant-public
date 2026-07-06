"""索引分组(老期冻结弧短语)存储与双区组装集成测试.

灰盒: 真实 ConversationService / MemoryAssembler / SQLite, 不涉及外部 LLM.
聚焦:
- 分组存储 round-trip (create_index_group → get_index_groups_up_to 过滤)
- 双区组装: <timeline> 弧短语(老期) + <index> 全索引(近期 bridge) 共存
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from langchain_core.messages import AIMessage

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


def _make_assembler(
    user_id: str,
    thread_id: str,
    *,
    total_budget: int = 300,
    index_budget: int = 10000,
) -> MemoryAssembler:
    return MemoryAssembler(
        agent_id=_AGENT_ID,
        agent_config=AgentConfig(
            agent_id=_AGENT_ID,
            memory=AgentMemoryConfig(
                total_char_budget=total_budget,
                index_char_budget=index_budget,
                include_todo_in_context=False,
            ),
        ),
        user_id=user_id,
        thread_id=thread_id,
    )


async def _seed_rounds(
    user_id: str, thread_id: str, count: int, *, start: int = 1, msg_len: int = 150
) -> None:
    conv_service = await create_conversation_service(
        user_id, thread_id, agent_id=_AGENT_ID
    )
    for offset in range(count):
        rn = start + offset
        await conv_service.create_conversation(
            user_message=f"第{rn}轮用户消息" + "x" * msg_len,
            assistant_response=f"第{rn}轮助手回复" + "x" * msg_len,
            user_id=user_id,
            thread_id=thread_id,
            agent_id=_AGENT_ID,
            round_number=rn,
            metadata={"topic": f"话题{rn}", "summary": f"摘要{rn}"},
        )


@pytest.mark.integration
class TestIndexGroupsIntegration:
    """分组存储 round-trip + 双区组装."""

    @pytest.mark.asyncio
    async def test_group_storage_roundtrip_and_filter(
        self,
        test_user,
        test_thread_id,
    ):
        """create_index_group → get_index_groups_up_to 按 round_end 过滤."""
        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        await conv_service.create_index_group(
            user_id=test_user,
            thread_id=test_thread_id,
            agent_id=_AGENT_ID,
            round_start=1,
            round_end=3,
            arc_phrase="早期健康档案",
        )
        await conv_service.create_index_group(
            user_id=test_user,
            thread_id=test_thread_id,
            agent_id=_AGENT_ID,
            round_start=4,
            round_end=6,
            arc_phrase="饮食记录",
        )

        up_to_5 = await conv_service.get_index_groups_up_to(
            test_user, test_thread_id, 5
        )
        assert len(up_to_5) == 1
        assert up_to_5[0].round_end == 3

        up_to_10 = await conv_service.get_index_groups_up_to(
            test_user, test_thread_id, 10
        )
        assert len(up_to_10) == 2
        assert up_to_10[0].round_start == 1
        assert up_to_10[1].round_start == 4

    @pytest.mark.asyncio
    async def test_two_region_render_timeline_and_bridge(
        self,
        test_user,
        test_thread_id,
    ):
        """冻结分组 + 近期 fine → 组装出 <timeline>(弧短语) + <index>(bridge)."""
        await _seed_rounds(test_user, test_thread_id, 10)

        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        # 冻结老期 run [1-3]
        await conv_service.create_index_group(
            user_id=test_user,
            thread_id=test_thread_id,
            agent_id=_AGENT_ID,
            round_start=1,
            round_end=3,
            arc_phrase="早期健康档案建立",
        )

        # 主历史预算 1000(ge 下限), 每轮~330字 → 仅装最近 3 轮 → index_end ≈ 7
        # index_budget=50: fine 行装不下 [1,7], 溢出 group(1-3) 为弧短语
        assembler = _make_assembler(
            test_user, test_thread_id, total_budget=1000, index_budget=50,
        )
        ctx = await assembler.assemble_memory_context(
            user_id=test_user, thread_id=test_thread_id
        )

        index_msg = next(
            (m for m in ctx.history_messages if isinstance(m, AIMessage)),
            None,
        )
        assert index_msg is not None
        content = str(index_msg.content)

        # 老期弧短语(budget 不足触发 group 溢出展示)
        assert "<timeline>" in content
        assert "早期健康档案建立" in content
        # 近期 fine 行(budget 内的最新轮次)
        assert "<index>" in content
