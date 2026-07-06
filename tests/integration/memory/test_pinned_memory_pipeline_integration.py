"""Pinned Memory 1-step 更新管线集成测试.

验证置顶记忆从 LLM 操作化分析 → apply_operations → DAO → SQLite →
缓存失效 → MemoryAssembler 读回的端到端闭环,
补充单元测试中 analyze_pinned_memory_update 与 apply_operations 被拆开 Mock 的缺口.

测试策略: 灰盒 - 真实 PinnedMemoryService / SimplePinnedMemoryManager / MemoryService /
SQLite / MemoryAssembler, 仅 Mock 真正的外部依赖 (SimpleContentAnalyzer).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import patch

import pytest

from src.agent.memory.local_memory import pinned_memory_service
from src.agent.memory.local_memory.assembler import MemoryAssembler
from src.agent.memory.local_memory.pinned_memory import SimplePinnedMemoryManager
from src.agent.memory.local_memory.pinned_memory_service import PinnedMemoryService
from src.config.agent_config import AgentConfig, AgentMemoryConfig
from src.core.types import (
    MemoryOperation,
    PinnedMemoryUpdateResult,
)
from src.storage.models.conversation import ConversationData
from src.storage.models.simple_pinned_memory import SimplePinnedMemoryType
from src.storage.service.service_factory import (
    create_memory_service,
)

_AGENT_ID = "test-agent"


class _StubEmbeddings:
    """可控向量桩: 让语义重复对映射到同一向量(余弦=1.0>=阈值)."""

    def __init__(self, vector_map: dict[str, list[float]]) -> None:
        self.vector_map = vector_map

    async def aembed_query(self, text: str) -> list[float]:
        return self.vector_map.get(text, [0.0, 0.0])

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.vector_map.get(t, [0.0, 0.0]) for t in texts]


async def _drain_pinned_bg_tasks() -> None:
    """等待所有置顶后台任务完成 (fire-and-forget)."""
    pending = list(pinned_memory_service.get_bg_tasks())
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _make_conv_data(
    user_id: str,
    thread_id: str,
    round_number: int,
    user_message: str,
    assistant_response: str = "收到",
) -> ConversationData:
    """构造测试用 ConversationData."""
    return ConversationData(
        user_id=user_id,
        thread_id=thread_id,
        user_message=user_message,
        assistant_response=assistant_response,
        round_number=round_number,
        timestamp=datetime.now(),
        agent_id=_AGENT_ID,
    )


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


@pytest.mark.integration
class TestPinnedMemoryPipelineIntegration:
    """Pinned Memory 1-step 管线端到端集成测试."""

    @pytest.mark.asyncio
    async def test_add_operation_writes_to_db_and_assembler_reads_back(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """LLM 返回 add 操作 → apply → DB 可读回 → assembler 读到新记忆.

        Mock 边界: llm_mocks["pinned"] 返回 add 操作
        协作链: analyze_pinned_memory_update → apply_operations → MemoryService → DAO → SQLite
                → cache clear → MemoryAssembler.assemble_memory_context 读回
        """
        llm_mocks[
            "pinned"
        ].analyze_pinned_memory_update.return_value = PinnedMemoryUpdateResult(
            has_operations=True,
            operations=[
                MemoryOperation(
                    action="add",
                    field="basic_info",
                    content="用户是软件工程师",
                ),
            ],
        )

        svc = PinnedMemoryService(test_user, test_thread_id, _AGENT_ID)
        await svc.update(
            _make_conv_data(test_user, test_thread_id, 1, "我是软件工程师")
        )

        mem_service = await create_memory_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        pinned = await mem_service.get_pinned_memory_as_dict(test_user, test_thread_id)
        assert "用户是软件工程师" in pinned.get("basic_info", "")

        assembler = _make_assembler(test_user, test_thread_id)
        ctx = await assembler.assemble_memory_context(test_user, test_thread_id)
        assert "<pinned_memory>" in ctx.system_prompt_extension
        assert "用户是软件工程师" in ctx.system_prompt_extension

    @pytest.mark.asyncio
    async def test_add_deduplicates_existing_content(
        self,
        test_user,
        test_thread_id,
    ):
        """预置 DB 已有条目 → 再次 add 同样内容 → 去重不重复写入.

        直接调用 SimplePinnedMemoryManager.apply_operations 验证去重返回值,
        并通过 DAO 确认 DB 行数不变.
        """
        mem_service = await create_memory_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        await mem_service.update_memory(
            SimplePinnedMemoryType.BASIC_INFO,
            "用户是软件工程师",
            test_user,
            test_thread_id,
        )

        manager = SimplePinnedMemoryManager(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        updated = await manager.apply_operations(
            [
                MemoryOperation(
                    action="add",
                    field="basic_info",
                    content="用户是软件工程师",
                ),
            ]
        )

        assert updated is False, "重复 add 应无实际变更"

        pinned = await mem_service.get_pinned_memory_as_dict(test_user, test_thread_id)
        lines = [
            line.strip()
            for line in pinned.get("basic_info", "").split("\n")
            if line.strip()
        ]
        assert lines.count("用户是软件工程师") == 1

    @pytest.mark.asyncio
    async def test_change_operation_updates_exact_match(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """预置 DB 有旧偏好 → LLM 返回 change → DB 更新为新偏好.

        change 操作依赖精确字符串匹配, 验证端到端替换.
        """
        mem_service = await create_memory_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        await mem_service.update_memory(
            SimplePinnedMemoryType.BASIC_INFO,
            "旧偏好",
            test_user,
            test_thread_id,
        )

        llm_mocks[
            "pinned"
        ].analyze_pinned_memory_update.return_value = PinnedMemoryUpdateResult(
            has_operations=True,
            operations=[
                MemoryOperation(
                    action="change",
                    field="basic_info",
                    old_content="旧偏好",
                    new_content="新偏好",
                ),
            ],
        )

        svc = PinnedMemoryService(test_user, test_thread_id, _AGENT_ID)
        await svc.update(
            _make_conv_data(test_user, test_thread_id, 1, "我的偏好变了")
        )

        pinned = await mem_service.get_pinned_memory_as_dict(test_user, test_thread_id)
        basic_info = pinned.get("basic_info", "")
        assert "新偏好" in basic_info
        assert "旧偏好" not in basic_info

        assembler = _make_assembler(test_user, test_thread_id)
        ctx = await assembler.assemble_memory_context(test_user, test_thread_id)
        assert "新偏好" in ctx.system_prompt_extension

    @pytest.mark.asyncio
    async def test_cache_invalidation_after_update(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """先 assemble 缓存旧内容 → update 新内容 → 再次 assemble 读到新内容.

        验证 apply_operations 成功后 _clear_related_cache 同时失效
        pinned memory 与 conversation 缓存, 下次 assemble 从 DB 重新读取.
        """
        mem_service = await create_memory_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        await mem_service.update_memory(
            SimplePinnedMemoryType.BASIC_INFO,
            "原始内容",
            test_user,
            test_thread_id,
        )

        assembler = _make_assembler(test_user, test_thread_id)
        ctx1 = await assembler.assemble_memory_context(test_user, test_thread_id)
        assert "原始内容" in ctx1.system_prompt_extension

        llm_mocks[
            "pinned"
        ].analyze_pinned_memory_update.return_value = PinnedMemoryUpdateResult(
            has_operations=True,
            operations=[
                MemoryOperation(
                    action="change",
                    field="basic_info",
                    old_content="原始内容",
                    new_content="新内容",
                ),
            ],
        )

        svc = PinnedMemoryService(test_user, test_thread_id, _AGENT_ID)
        await svc.update(
            _make_conv_data(test_user, test_thread_id, 1, "内容更新了")
        )

        ctx2 = await assembler.assemble_memory_context(test_user, test_thread_id)
        assert "新内容" in ctx2.system_prompt_extension
        assert "原始内容" not in ctx2.system_prompt_extension

    @pytest.mark.asyncio
    async def test_fire_and_forget_round_does_not_lose_update(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """on_conversation_round 触发后台更新 → drain 后 DB 反映更新.

        并发触发两轮不同内容的 add, 通过 _pinned_lock 串行化,
        验证最终两条内容均存在 (无 lost update).
        """
        llm_mocks[
            "pinned"
        ].analyze_pinned_memory_update.side_effect = [
            PinnedMemoryUpdateResult(
                has_operations=True,
                operations=[
                    MemoryOperation(
                        action="add",
                        field="basic_info",
                        content="第一条更新",
                    ),
                ],
            ),
            PinnedMemoryUpdateResult(
                has_operations=True,
                operations=[
                    MemoryOperation(
                        action="add",
                        field="basic_info",
                        content="第二条更新",
                    ),
                ],
            ),
        ]

        svc = PinnedMemoryService(test_user, test_thread_id, _AGENT_ID)
        svc.on_conversation_round(
            _make_conv_data(test_user, test_thread_id, 1, "消息一")
        )
        svc.on_conversation_round(
            _make_conv_data(test_user, test_thread_id, 2, "消息二")
        )
        await _drain_pinned_bg_tasks()

        mem_service = await create_memory_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        pinned = await mem_service.get_pinned_memory_as_dict(test_user, test_thread_id)
        basic_info = pinned.get("basic_info", "")
        assert "第一条更新" in basic_info
        assert "第二条更新" in basic_info

    @pytest.mark.asyncio
    async def test_delete_operation_requires_exact_match(
        self,
        test_user,
        test_thread_id,
    ):
        """delete 操作精确匹配删除目标, 未命中则保留原内容."""
        mem_service = await create_memory_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        await mem_service.update_memory(
            SimplePinnedMemoryType.BASIC_INFO,
            "待保留项\n待删除项",
            test_user,
            test_thread_id,
        )

        manager = SimplePinnedMemoryManager(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        updated = await manager.apply_operations(
            [
                MemoryOperation(
                    action="delete",
                    field="basic_info",
                    content="待删除项",
                ),
            ]
        )

        assert updated is True

        pinned = await mem_service.get_pinned_memory_as_dict(test_user, test_thread_id)
        basic_info = pinned.get("basic_info", "")
        assert "待删除项" not in basic_info
        assert "待保留项" in basic_info

        updated_miss = await manager.apply_operations(
            [
                MemoryOperation(
                    action="delete",
                    field="basic_info",
                    content="不存在的项",
                ),
            ]
        )
        assert updated_miss is False

    @pytest.mark.asyncio
    async def test_add_semantic_duplicate_skipped_end_to_end(
        self,
        test_user,
        test_thread_id,
    ):
        """预置 DB 已有条目 → add 语义重复(换表述) → 语义去重跳过, 不写库.

        端到端验证 apply_operations 的 add 分支: 经嵌入向量判定语义重复后
        不产生新行. Mock 边界仅 create_embeddings(注入可控向量),
        其余 PinnedMemoryService / MemoryService / SQLite 均真实.
        """
        mem_service = await create_memory_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        await mem_service.update_memory(
            SimplePinnedMemoryType.BASIC_INFO,
            "用户是软件工程师",
            test_user,
            test_thread_id,
        )

        # 两条不同表述映射到同一向量 -> 余弦=1.0 >= 阈值(0.90)
        stub = _StubEmbeddings(
            {
                "用户是软件工程师": [1.0, 0.0],
                "从事软件开发工作": [1.0, 0.0],
            }
        )
        manager = SimplePinnedMemoryManager(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        with patch(
            "src.inference.embeddings.embeddings.create_embeddings",
            return_value=stub,
        ):
            updated = await manager.apply_operations(
                [
                    MemoryOperation(
                        action="add",
                        field="basic_info",
                        content="从事软件开发工作",
                    ),
                ]
            )

        assert updated is False, "语义重复 add 应被去重, 无实际变更"

        pinned = await mem_service.get_pinned_memory_as_dict(test_user, test_thread_id)
        basic_info = pinned.get("basic_info", "")
        assert "从事软件开发工作" not in basic_info
        assert "用户是软件工程师" in basic_info
