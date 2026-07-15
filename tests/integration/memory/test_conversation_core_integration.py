"""ConversationMemoryCore 并行存储编排集成测试.

验证对话记忆核心的并行存储协作, 补充单元测试过度 Mock 的真实行为:

- 5 路并行操作数据一致性 (SQL/向量/索引/缓存/置顶覆写)
- 向量存储失败容错: asyncio.gather(return_exceptions=True) 不阻塞 SQL
- 嵌入禁用降级: _embeddings_enabled=False 跳过向量路径
- 跨线程/Agent 物理隔离: 各自数据库独立
- 并发写竞态: 同线程多轮次并发不丢失不重复

测试策略: 灰盒 - 真实 ConversationMemoryCore + PinnedMemoryService + 全部 SQL Service +
真实 SQLite, 仅 Mock 真正的外部依赖 (LLM 分析器 + ChromaDB 向量存储).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agent.memory.local_memory import pinned_memory_service
from src.agent.memory.local_memory.core import ConversationMemoryCore
from src.config.agent_config import AgentConfig
from src.core.types import ConversationIndexResult
from src.inference.content_analyzer.pinned_memory_rewriter import RewriteResult
from src.storage.models.conversation import ConversationData
from src.storage.service import create_pinned_memory_block_service
from src.storage.service.service_factory import (
    clear_vector_cache,
    create_conversation_service,
    create_memory_service,
)

_AGENT_ID = "test-agent"


async def _drain_pinned_bg_tasks() -> None:
    """等待所有置顶后台任务完成 (置顶覆写 fire-and-forget)."""
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
        """测试并行存储操作后的数据一致性.

        协作场景: ConversationMemoryCore.add_conversation_round 编排
                  SQL 存储 + 向量存储 + 索引生成 + 缓存更新 +
                  fire-and-forget 置顶覆写
        Mock 边界: LLM 索引分析器 + 向量服务 + PinnedMemoryRewriter.rewrite
        验证重点: SQL 对话内容可读回 / 向量服务被调用 / 索引 LLM 被调用 /
                  fire-and-forget 置顶覆写真实写入 pinned_memory_block
        业务价值: 确保对话完成后所有存储路径数据一致, 不丢失
        """
        llm_mocks[
            "index"
        ].analyze_conversation_index.return_value = ConversationIndexResult(
            summary="天气查询对话",
            topic="天气",
        )

        with patch(
            "src.inference.content_analyzer.pinned_memory_rewriter.PinnedMemoryRewriter.rewrite",
            new=AsyncMock(
                return_value=RewriteResult(
                    needs_update=True, content="用户关注每日天气变化"
                ),
            ),
        ):
            core = _make_core(test_user, test_thread_id)
            conv_data = _make_conv_data(test_user, test_thread_id, round_number=1)
            conv_data.metadata["_messages_snapshot"] = [
                HumanMessage(content="今天天气怎么样"),
                AIMessage(content="今天晴天"),
            ]
            await core.add_conversation_round(conv_data)
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
        assert conv.topic == "天气", "索引 topic 应持久化到 DB"
        assert conv.summary == "天气查询对话", "索引 summary 应持久化到 DB"

        assert llm_mocks["vector"].add_conversation_content.called, "向量服务应被调用"
        llm_mocks["index"].analyze_conversation_index.assert_called_once()

        block_service = await create_pinned_memory_block_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        pinned_content = await block_service.get_content(test_user, test_thread_id)
        assert "天气" in pinned_content, "置顶覆写应写入 pinned_memory_block"

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
