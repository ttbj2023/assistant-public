"""记忆子系统集成测试共享 fixture.

提供置顶/索引 run 模块状态的 autouse 清理 fixture, 以及可选的 LLM/向量 Mock.

DB 全局状态 (engine dispose / 锁重建 / Service 与记忆缓存清空) 的跨事件循环
隔离由 tests/integration/conftest.py 的 _reset_db_and_service_state 统一负责,
覆盖全部集成测试, 此处不再重复.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack, suppress
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.memory.local_memory import index_run_service, pinned_memory_service


async def _drain_bg_tasks() -> None:
    """取消并等待 pinned/index 的 fire-and-forget 后台任务, 释放其持有的 aiosqlite 连接.

    这些任务在 loop 关闭前若未结束, 其 session 的 aiosqlite 连接会被 GC 触发
    Connection.__del__ ResourceWarning; 故 teardown 显式取消并 await.
    """
    tasks = [*pinned_memory_service.get_bg_tasks(), *index_run_service.get_bg_tasks()]
    for t in tasks:
        if not t.done():
            t.cancel()
    for t in tasks:
        with suppress(asyncio.CancelledError, Exception):
            await t


@pytest.fixture(autouse=True)
async def _reset_pinned_module_state() -> AsyncIterator[None]:
    """每个测试前后清理置顶/索引模块级状态 (锁/审计轮次/后台任务).

    teardown 先 drain 后台任务 (释放 aiosqlite 连接), 再清模块状态, 避免 loop
    关闭时未完成任务泄漏连接. xdist 每进程独立, 同进程内测试共享模块状态,
    需清理避免互相污染.
    """
    await _drain_bg_tasks()
    pinned_memory_service.clear_module_state()
    index_run_service.clear_module_state()
    yield
    await _drain_bg_tasks()
    pinned_memory_service.clear_module_state()
    index_run_service.clear_module_state()


@pytest.fixture
def llm_mocks() -> Iterator[dict[str, MagicMock]]:
    """统一 Mock 三个 LLM 分析器 + 向量服务, 返回可配置的 mock 句柄.

    Mock 边界 (仅外部依赖):
        - SimpleContentAnalyzer (索引分析 + 置顶更新分析, 调真实 LLM API)
        - PinnedMemoryAuditAnalyzer (置顶审计, 调真实 LLM API)
        - create_vector_service (ChromaDB 向量存储, 双路径 patch)

    保留真实 (内部组件):
        ConversationMemoryCore / PinnedMemoryService / ConversationDataService /
        ConversationService / MemoryService / SimplePinnedMemoryManager / SQLite.
    """
    index_analyzer = MagicMock()
    index_analyzer.analyze_conversation_index = AsyncMock()

    pinned_analyzer = MagicMock()
    pinned_analyzer.analyze_pinned_memory_update = AsyncMock()

    audit_analyzer = MagicMock()
    audit_analyzer.audit = AsyncMock()

    arc_analyzer = MagicMock()
    arc_analyzer.distill = AsyncMock(return_value="话题弧短语")

    vector_service = MagicMock()
    vector_service.add_conversation_content = AsyncMock(return_value="fake_vector_id")

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "src.inference.content_analyzer.simple_analyzer.get_content_analyzer",
                return_value=index_analyzer,
            )
        )
        stack.enter_context(
            patch(
                "src.inference.content_analyzer.simple_analyzer.SimpleContentAnalyzer",
                return_value=pinned_analyzer,
            )
        )
        stack.enter_context(
            patch(
                "src.inference.content_analyzer.pinned_memory_audit_analyzer.PinnedMemoryAuditAnalyzer",
                return_value=audit_analyzer,
            )
        )
        stack.enter_context(
            patch(
                "src.inference.content_analyzer.index_arc_analyzer.IndexArcAnalyzer",
                return_value=arc_analyzer,
            )
        )
        stack.enter_context(
            patch(
                "src.storage.service.create_vector_service",
                return_value=vector_service,
            )
        )
        stack.enter_context(
            patch(
                "src.storage.service.service_factory.create_vector_service",
                return_value=vector_service,
            )
        )
        stack.enter_context(
            patch(
                "src.agent.memory.local_memory.core.create_vector_service",
                return_value=vector_service,
            )
        )
        yield {
            "index": index_analyzer,
            "pinned": pinned_analyzer,
            "audit": audit_analyzer,
            "arc": arc_analyzer,
            "vector": vector_service,
        }
