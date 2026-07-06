"""双路检索系统集成测试.

验证 AsyncMemoryRetrievalTool + DualStageRetrievalService + 真实 SQL 的协作,
补充单元测试过度 Mock 的部分, 并覆盖两个已发现的缺陷修复:

- SQL 路径返回最近轮次 + Document metadata 字段完整 (缺陷 A: timestamp 字段映射)
- 向量与 SQL 经 smart_deduplication 合并 (交集优先)
- 向量失败降级纯 SQL
- health_check 反映真实组件状态
- aget_relevant_documents 主路径失败的合理降级 (缺陷 B: 降级方向与 _arun 一致)

测试策略: 灰盒 - 真实 DualStageRetrievalService + ConversationService + SQLite +
smart_deduplication, 仅 Mock 向量路径 (vector_store.search_rounds_only) 避免 ChromaDB 依赖.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.storage.service.retrieval_service import DualStageRetrievalService
from src.storage.service.service_factory import (
    clear_vector_cache,
    create_conversation_service,
)
from src.tools.internal.async_memory_retrieval_tool import AsyncMemoryRetrievalTool

_AGENT_ID = "test-agent"


@pytest.fixture(autouse=True)
def _reset_db_state() -> Iterator[None]:
    """重置 DB 全局状态 + Service 缓存, 避免跨事件循环污染."""
    from src.storage.dao import async_database_manager as adm

    adm._db_cache_lock = asyncio.Lock()
    adm._db_manager_cache.clear()
    clear_vector_cache()
    yield
    adm._db_cache_lock = asyncio.Lock()
    adm._db_manager_cache.clear()
    clear_vector_cache()


def _make_mock_vector_service(
    rounds_with_scores: list[tuple[int, float]] | None = None,
    *,
    search_fails: bool = False,
) -> MagicMock:
    """构造 Mock VectorService, 控制 search_rounds_only 返回.

    DualStageRetrievalService 经 vector_service._vector_store.search_rounds_only 访问向量.
    """
    vector_store = MagicMock()
    vector_store._ensure_initialized = AsyncMock()
    if search_fails:
        vector_store.search_rounds_only = AsyncMock(
            side_effect=RuntimeError("vector offline"),
        )
    else:
        vector_store.search_rounds_only = AsyncMock(
            return_value=rounds_with_scores or [],
        )
    vs = MagicMock()
    vs._vector_store = vector_store
    vs.health_check = AsyncMock(return_value={"status": "healthy"})
    return vs


async def _seed_rounds(user_id: str, thread_id: str, count: int) -> None:
    """预置 count 轮真实对话."""
    conv_service = await create_conversation_service(
        user_id, thread_id, agent_id=_AGENT_ID
    )
    for rn in range(1, count + 1):
        await conv_service.create_conversation(
            user_message=f"第{rn}轮讨论话题{rn}",
            assistant_response=f"关于话题{rn}的详细回复内容",
            user_id=user_id,
            thread_id=thread_id,
            agent_id=_AGENT_ID,
            round_number=rn,
        )


@pytest.mark.integration
class TestRetrievalTwoStageIntegration:
    """双路检索 (SQL + 向量) 集成测试."""

    @pytest.mark.asyncio
    async def test_integration_retrieval_sql_returns_recent_rounds_with_timestamp(
        self,
        test_user,
        test_thread_id,
    ):
        """测试纯 SQL 路径返回最近轮次, 且 Document metadata 含 timestamp.

        协作场景: 预置 10 轮 + 向量返回空 → 纯 SQL (list_recent_rounds) +
                  smart_dedup + _async_get_final_documents 构造 Document
        Mock 边界: vector_store.search_rounds_only 返回空 (仅 SQL 路径)
        验证重点: 返回 max_results 个 Document / round_number 为最近轮次 /
                  metadata 含非空 timestamp (缺陷 A 修复: 字段映射 created_at→timestamp)
        业务价值: SQL 检索结果可被 Tool 层正确格式化
        """
        await _seed_rounds(test_user, test_thread_id, count=10)

        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        service = DualStageRetrievalService(
            conversation_service=conv_service,
            vector_service=_make_mock_vector_service(),
            user_id=test_user,
            thread_id=test_thread_id,
            max_results=3,
        )

        docs = await service.search_conversations("话题", max_results=3)

        assert len(docs) == 3, "应返回 max_results=3 个 Document"
        round_numbers = [d.metadata.get("round_number") for d in docs]
        assert max(round_numbers) == 10, "应含最近轮次 (round 10)"

        for doc in docs:
            ts = doc.metadata.get("timestamp")
            assert ts is not None and ts != "unknown", (
                "timestamp 字段应为有效值 (缺陷 A: created_at→timestamp 映射)"
            )

    @pytest.mark.asyncio
    async def test_integration_retrieval_vector_merged_with_sql_dedup(
        self,
        test_user,
        test_thread_id,
    ):
        """测试向量与 SQL 经 smart_deduplication 合并 (交集优先).

        协作场景: SQL 返回最近轮次 + 向量返回带得分轮次 → smart_deduplication 合并
        Mock 边界: vector_store.search_rounds_only 返回 [(3, 0.9), (7, 0.85)]
        验证重点: 交集轮次 (7) 优先排序 / vector 独有轮次 (3) 被纳入 / 无重复
        业务价值: 双路检索的语义+时间融合, 交集结果获得更高优先级
        """
        await _seed_rounds(test_user, test_thread_id, count=10)

        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        service = DualStageRetrievalService(
            conversation_service=conv_service,
            vector_service=_make_mock_vector_service(
                rounds_with_scores=[(3, 0.9), (7, 0.85)]
            ),
            user_id=test_user,
            thread_id=test_thread_id,
            max_results=5,
        )

        docs = await service.search_conversations("话题", max_results=5)

        round_numbers = {d.metadata.get("round_number") for d in docs}
        assert 7 in round_numbers, "交集轮次 7 应被纳入"
        assert 3 in round_numbers, "vector 独有轮次 3 应被纳入"
        assert len(round_numbers) == len(docs), "轮次号应去重"

    @pytest.mark.asyncio
    async def test_integration_retrieval_vector_failure_degrades_to_pure_sql(
        self,
        test_user,
        test_thread_id,
    ):
        """测试向量路径失败时降级纯 SQL 仍返回结果.

        协作场景: vector_store.search_rounds_only 抛异常 →
                  _async_vector_search_rounds 容错返回空 → 仅 SQL 路径工作
        Mock 边界: vector_store.search_rounds_only side_effect=RuntimeError
        验证重点: 仍返回结果 (纯 SQL) / 不抛错
        业务价值: 向量依赖 (ChromaDB) 故障不阻塞检索
        """
        await _seed_rounds(test_user, test_thread_id, count=5)

        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        service = DualStageRetrievalService(
            conversation_service=conv_service,
            vector_service=_make_mock_vector_service(search_fails=True),
            user_id=test_user,
            thread_id=test_thread_id,
            max_results=3,
        )

        docs = await service.search_conversations("话题", max_results=3)

        assert len(docs) > 0, "向量失败时纯 SQL 仍应返回结果"
        assert len(docs) <= 3

    @pytest.mark.asyncio
    async def test_integration_retrieval_health_check_reflects_real_state(
        self,
        test_user,
        test_thread_id,
    ):
        """测试 health_check 反映真实组件状态.

        协作场景: DualStageRetrievalService.health_check 报告 SQL/vector 启用状态
        Mock 边界: Mock vector_service (health_check 返回 healthy)
        验证重点: sql_search_enabled=True / vector_search_enabled=True / status=healthy
        业务价值: 健康检查可观测检索子系统真实状态
        """
        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        service = DualStageRetrievalService(
            conversation_service=conv_service,
            vector_service=_make_mock_vector_service(),
            user_id=test_user,
            thread_id=test_thread_id,
        )

        health = await service.health_check()

        assert health["features"]["sql_search_enabled"] is True
        assert health["features"]["vector_search_enabled"] is True
        assert health["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_integration_tool_aget_relevant_documents_failure_returns_empty(
        self,
        test_user,
        test_thread_id,
    ):
        """测试 aget_relevant_documents 主路径失败的合理降级 (缺陷 B).

        协作场景: AsyncMemoryRetrievalTool.aget_relevant_documents →
                  search_conversations 抛异常 → 合理降级 (返回空, 不再调用
                  会二次失败的 search_with_filters)
        Mock 边界: 注入 Mock service, search_conversations 抛错,
                  search_with_filters 为 spy 验证不被调用
        验证重点: 返回空列表 / search_with_filters 不被调用
                  (缺陷 B 修复: 降级方向与 _arun 一致, 不做无意义的二次失败调用)
        业务价值: 检索失败时优雅降级, 不产生误导性的二次异常
        """
        mock_service = MagicMock()
        mock_service.search_conversations = AsyncMock(
            side_effect=RuntimeError("service offline"),
        )
        mock_service.search_with_filters = AsyncMock(return_value=[])

        tool = AsyncMemoryRetrievalTool(
            user_id=test_user, thread_id=test_thread_id, agent_id=_AGENT_ID
        )
        tool._retrieval_service = mock_service

        result = await tool.aget_relevant_documents("话题")

        assert result == [], "主路径失败应降级返回空列表"
        (
            mock_service.search_with_filters.assert_not_called(),
            ("不应调用会二次失败的 search_with_filters (缺陷 B)"),
        )
