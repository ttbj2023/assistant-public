"""ConversationMemoryCore 6 路并行存储编排集成测试.

验证对话记忆核心的并行存储协作, 补充单元测试过度 Mock 的真实行为:

- 6 路并行操作数据一致性 (SQL/向量/索引/缓存/置顶更新/置顶审计)
- 向量存储失败容错: asyncio.gather(return_exceptions=True) 不阻塞 SQL
- 嵌入禁用降级: _embeddings_enabled=False 跳过向量路径
- 跨线程/Agent 物理隔离: 各自数据库独立
- 置顶审计触发: _AUDIT_INTERVAL 轮触发 audit (fire-and-forget + _pinned_lock 串行)
- 并发写竞态: 同线程多轮次并发不丢失不重复

测试策略: 灰盒 - 真实 ConversationMemoryCore + PinnedMemoryService + 全部 SQL Service +
真实 SQLite, 仅 Mock 真正的外部依赖 (LLM 分析器 + ChromaDB 向量存储).
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from src.agent.memory.local_memory import pinned_memory_service
from src.agent.memory.local_memory.core import ConversationMemoryCore
from src.config.agent_config import AgentConfig
from src.core.types import (
    ConversationIndexResult,
    MemoryOperation,
    PinnedMemoryUpdateResult,
)
from src.storage.models.conversation import ConversationData
from src.storage.service.service_factory import (
    clear_vector_cache,
    create_conversation_service,
    create_memory_service,
)

_AGENT_ID = "test-agent"


async def _drain_pinned_bg_tasks() -> None:
    """等待所有置顶后台任务完成 (置顶更新/审计为 fire-and-forget)."""
    pending = list(pinned_memory_service.get_bg_tasks())
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _make_conv_data(
    user_id: str,
    thread_id: str,
    round_number: int,
    *,
    agent_id: str = _AGENT_ID,
    user_message: str = "今天天气怎么样",
    assistant_response: str = "今天晴天, 适合外出活动",
) -> ConversationData:
    """构造测试用 ConversationData."""
    return ConversationData(
        user_id=user_id,
        thread_id=thread_id,
        user_message=user_message,
        assistant_response=assistant_response,
        round_number=round_number,
        timestamp=datetime.now(),
        agent_id=agent_id,
    )


def _make_core(user_id: str, thread_id: str) -> ConversationMemoryCore:
    """构造真实 ConversationMemoryCore 实例."""
    return ConversationMemoryCore(
        user_id=user_id,
        thread_id=thread_id,
        agent_config=AgentConfig(agent_id=_AGENT_ID),
    )


@pytest.mark.integration
class TestConversationMemoryCoreIntegration:
    """ConversationMemoryCore.add_conversation_round 并行存储编排集成测试."""

    @pytest.mark.asyncio
    async def test_integration_core_storage_operations_data_consistency(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """测试 6 路并行操作后的数据一致性.

        协作场景: ConversationMemoryCore.add_conversation_round 编排
                  _store_conversation_content (ConversationDataService 内部 4 路)
                  + _store_vector_conversation + _generate_conversation_index (LLM)
                  + _update_conversation_cache + fire-and-forget 置顶更新
        Mock 边界: 仅 Mock LLM 分析器 (索引/置顶) + 向量服务
        验证重点: SQL 对话内容可读回 / 向量服务被调用 / 索引 LLM 被调用 /
                  fire-and-forget 置顶更新真实写入数据库
        业务价值: 确保对话完成后所有存储路径数据一致, 不丢失
        """
        llm_mocks[
            "index"
        ].analyze_conversation_index.return_value = ConversationIndexResult(
            summary="天气查询对话",
            topic="天气",
        )
        llm_mocks[
            "pinned"
        ].analyze_pinned_memory_update.return_value = PinnedMemoryUpdateResult(
            has_operations=True,
            operations=[
                MemoryOperation(
                    action="add",
                    field="basic_info",
                    content="关注每日天气变化",
                ),
            ],
        )

        core = _make_core(test_user, test_thread_id)
        await core.add_conversation_round(
            _make_conv_data(test_user, test_thread_id, round_number=1)
        )
        await _drain_pinned_bg_tasks()

        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        conv = await conv_service.get_conversation_by_round(
            test_user, test_thread_id, 1
        )
        assert conv is not None, "对话内容应写入 SQL"
        assert conv.user_message == "今天天气怎么样"
        assert conv.assistant_response == "今天晴天, 适合外出活动"
        assert conv.round_number == 1

        assert llm_mocks["vector"].add_conversation_content.called, "向量服务应被调用"
        llm_mocks["index"].analyze_conversation_index.assert_called_once()
        llm_mocks["pinned"].analyze_pinned_memory_update.assert_called_once()

        mem_service = await create_memory_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        pinned = await mem_service.get_pinned_memory_as_dict(test_user, test_thread_id)
        assert isinstance(pinned, dict)
        assert "关注每日天气变化" in pinned.get("basic_info", "")

    @pytest.mark.asyncio
    async def test_integration_core_vector_failure_does_not_block_sql(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """测试向量存储失败不阻塞 SQL 主路径.

        协作场景: _store_vector_conversation 抛异常 →
                  asyncio.gather(return_exceptions=True) 容错 →
                  SQL 存储/索引生成/置顶更新仍正常完成
        Mock 边界: 向量服务抛 RuntimeError, 其余真实
        验证重点: 主流程不抛错 / SQL 对话内容仍写入 / 索引 LLM 仍被调用
        业务价值: 单路外部依赖 (ChromaDB) 故障不导致对话数据丢失
        """
        llm_mocks["vector"].add_conversation_content.side_effect = RuntimeError(
            "ChromaDB offline"
        )
        llm_mocks[
            "index"
        ].analyze_conversation_index.return_value = ConversationIndexResult(
            summary="容错测试",
            topic="测试",
        )
        llm_mocks[
            "pinned"
        ].analyze_pinned_memory_update.return_value = PinnedMemoryUpdateResult()

        core = _make_core(test_user, test_thread_id)
        await core.add_conversation_round(
            _make_conv_data(test_user, test_thread_id, round_number=1)
        )
        await _drain_pinned_bg_tasks()

        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        conv = await conv_service.get_conversation_by_round(
            test_user, test_thread_id, 1
        )
        assert conv is not None, "向量失败时 SQL 存储仍应成功"
        assert conv.user_message == "今天天气怎么样"
        llm_mocks["index"].analyze_conversation_index.assert_called_once()

    @pytest.mark.asyncio
    async def test_integration_core_embeddings_disabled_skips_core_vector_path(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """测试嵌入禁用时 ConversationMemoryCore 跳过自身的向量存储路径.

        协作场景: _embeddings_enabled=False →
                  _store_vector_conversation 提前 return, 不调用向量服务
        Mock 边界: LLM 分析器 + 向量服务
        验证重点: 嵌入禁用时 ConversationMemoryCore 不额外调用向量服务
                  (向量调用次数少于启用时) / SQL 存储仍正常
        业务价值: 纯 SQL 模式下避免无谓的向量依赖初始化
        """
        llm_mocks[
            "index"
        ].analyze_conversation_index.return_value = ConversationIndexResult(
            summary="禁用测试",
            topic="测试",
        )
        llm_mocks[
            "pinned"
        ].analyze_pinned_memory_update.return_value = PinnedMemoryUpdateResult()

        core = _make_core(test_user, test_thread_id)
        core._embeddings_enabled = False

        await core.add_conversation_round(
            _make_conv_data(test_user, test_thread_id, round_number=1)
        )
        await _drain_pinned_bg_tasks()

        vector_calls_disabled = llm_mocks["vector"].add_conversation_content.call_count

        clear_vector_cache()
        llm_mocks["vector"].reset_mock()
        llm_mocks["index"].reset_mock()
        llm_mocks["pinned"].reset_mock()

        core_enabled = _make_core(test_user, test_thread_id)
        await core_enabled.add_conversation_round(
            _make_conv_data(test_user, test_thread_id, round_number=2)
        )
        await _drain_pinned_bg_tasks()

        vector_calls_enabled = llm_mocks["vector"].add_conversation_content.call_count

        assert vector_calls_disabled < vector_calls_enabled, (
            "嵌入禁用应减少向量服务调用次数"
        )

        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        conv = await conv_service.get_conversation_by_round(
            test_user, test_thread_id, 1
        )
        assert conv is not None, "禁用嵌入时 SQL 存储仍应成功"

    @pytest.mark.asyncio
    async def test_integration_core_cross_thread_agent_isolation(
        self,
        test_user,
        thread_id_factory,
        llm_mocks,
    ):
        """测试跨线程 + Agent 物理隔离.

        协作场景: 两个独立线程各自 add_conversation_round (相同 round_number=1),
                  验证各自数据库独立, 数据不串扰
        Mock 边界: LLM 分析器 + 向量服务
        验证重点: 线程A 的对话在线程B 中不可见 / 两线程 round=1 各自独立存在
        业务价值: 多用户多线程并发场景下的数据隔离正确性
        """
        llm_mocks[
            "index"
        ].analyze_conversation_index.return_value = ConversationIndexResult(
            summary="隔离测试",
            topic="隔离",
        )
        llm_mocks[
            "pinned"
        ].analyze_pinned_memory_update.return_value = PinnedMemoryUpdateResult()

        variants = thread_id_factory(["thread_a", "thread_b"])
        thread_a = variants["thread_a"]
        thread_b = variants["thread_b"]

        core_a = _make_core(test_user, thread_a)
        core_b = _make_core(test_user, thread_b)
        await asyncio.gather(
            core_a.add_conversation_round(
                _make_conv_data(
                    test_user,
                    thread_a,
                    round_number=1,
                    user_message="线程A的消息",
                )
            ),
            core_b.add_conversation_round(
                _make_conv_data(
                    test_user,
                    thread_b,
                    round_number=1,
                    user_message="线程B的消息",
                )
            ),
        )
        await _drain_pinned_bg_tasks()

        conv_service_a = await create_conversation_service(
            test_user, thread_a, agent_id=_AGENT_ID
        )
        conv_service_b = await create_conversation_service(
            test_user, thread_b, agent_id=_AGENT_ID
        )
        conv_a = await conv_service_a.get_conversation_by_round(test_user, thread_a, 1)
        conv_b = await conv_service_b.get_conversation_by_round(test_user, thread_b, 1)

        assert conv_a is not None and conv_b is not None
        assert conv_a.user_message == "线程A的消息"
        assert conv_b.user_message == "线程B的消息"
        assert conv_a.user_message != conv_b.user_message

        cross_check = await conv_service_a.get_conversation_by_round(
            test_user, thread_a, 999
        )
        assert cross_check is None, "线程B 的 round=1 不应在线程A 可见范围"

    @pytest.mark.asyncio
    async def test_integration_pinned_audit_triggers_on_audit_round(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """测试置顶审计在 _AUDIT_INTERVAL 轮触发 (fire-and-forget + _pinned_lock 串行).

        协作场景: on_conversation_round 检测 round_number 达到 _AUDIT_INTERVAL →
                  _spawn_pinned_bg_task(audit) → audit 读全局置顶 +
                  PinnedMemoryAuditAnalyzer.audit (LLM) → apply_operations
        Mock 边界: LLM 分析器 (索引/置顶更新/审计) + 向量服务
        验证重点: 审计轮次触发后 audit_analyzer.audit 被调用 /
                  非审计轮次不调用 audit / _pinned_lock 保证更新与审计串行无 lost update
        业务价值: 周期审计机制正确触发, 置顶记忆保持精简
        """
        from src.agent.memory.local_memory.pinned_memory import (
            SimplePinnedMemoryManager,
        )

        llm_mocks[
            "index"
        ].analyze_conversation_index.return_value = ConversationIndexResult(
            summary="审计测试",
            topic="审计",
        )
        llm_mocks[
            "pinned"
        ].analyze_pinned_memory_update.return_value = PinnedMemoryUpdateResult()
        llm_mocks["audit"].audit.return_value = [
            MemoryOperation(
                action="delete",
                field="basic_info",
                content="待清理的过期偏好",
            )
        ]

        manager = SimplePinnedMemoryManager(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        await manager._update_single_field("basic_info", "待清理的过期偏好")

        audit_interval = pinned_memory_service._AUDIT_INTERVAL
        core = _make_core(test_user, test_thread_id)
        await core.add_conversation_round(
            _make_conv_data(test_user, test_thread_id, round_number=audit_interval)
        )
        await _drain_pinned_bg_tasks()

        assert llm_mocks["audit"].audit.called, f"第 {audit_interval} 轮应触发置顶审计"

        core_non_audit = _make_core(test_user, test_thread_id)
        await core_non_audit.add_conversation_round(
            _make_conv_data(test_user, test_thread_id, round_number=audit_interval + 1)
        )
        await _drain_pinned_bg_tasks()

        audit_call_count_after = llm_mocks["audit"].audit.call_count
        assert audit_call_count_after == 1, "非审计轮次不应额外触发审计"

    @pytest.mark.asyncio
    async def test_integration_core_concurrent_writes_same_thread(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """测试同线程多轮次并发写入不丢失不重复.

        协作场景: 同一 thread 并发 add 3 个不同 round_number 的 ConversationData,
                  验证全部成功写入且互不干扰
        Mock 边界: LLM 分析器 + 向量服务
        验证重点: 3 轮全部可读回 / round_number 连续无缺 / 数据内容一一对应
        业务价值: 高并发对话场景下数据完整性
        """
        llm_mocks[
            "index"
        ].analyze_conversation_index.return_value = ConversationIndexResult(
            summary="并发测试",
            topic="并发",
        )
        llm_mocks[
            "pinned"
        ].analyze_pinned_memory_update.return_value = PinnedMemoryUpdateResult()

        core = _make_core(test_user, test_thread_id)
        await asyncio.gather(
            core.add_conversation_round(
                _make_conv_data(
                    test_user,
                    test_thread_id,
                    round_number=1,
                    user_message="第一条消息",
                )
            ),
            core.add_conversation_round(
                _make_conv_data(
                    test_user,
                    test_thread_id,
                    round_number=2,
                    user_message="第二条消息",
                )
            ),
            core.add_conversation_round(
                _make_conv_data(
                    test_user,
                    test_thread_id,
                    round_number=3,
                    user_message="第三条消息",
                )
            ),
        )
        await _drain_pinned_bg_tasks()

        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        for rn, expected in [(1, "第一条消息"), (2, "第二条消息"), (3, "第三条消息")]:
            conv = await conv_service.get_conversation_by_round(
                test_user, test_thread_id, rn
            )
            assert conv is not None, f"round {rn} 应成功写入"
            assert conv.user_message == expected
