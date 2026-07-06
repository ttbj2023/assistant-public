"""ConversationMemoryCore 核心功能测试

测试对话记忆核心的6个并行操作、数据一致性、异常处理等关键功能。
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.memory.local_memory import pinned_memory_service
from src.agent.memory.local_memory.core import ConversationMemoryCore
from src.config.agent_config import AgentConfig
from src.core.types import PinnedMemoryUpdateResult
from tests.mocks.memory.local_memory import (
    create_mock_conversation_data,
)
from tests.unit.memory.local_memory.test_base import (
    AssertionMixin,
    AsyncTestMixin,
    BaseLocalMemoryTest,
    MockMixin,
)


async def _drain_pinned_bg_tasks() -> None:
    """等待所有置顶后台任务完成(置顶更新/审计已转 fire-and-forget)."""
    pending = list(pinned_memory_service.get_bg_tasks())
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture(autouse=True)
def _reset_pinned_module_state():
    """每个测试前后清理置顶模块级状态(锁/审计轮次/后台任务集).

    xdist 每进程独立, 同进程内测试共享模块状态, 需清理避免互相污染.
    """
    pinned_memory_service.clear_module_state()
    yield
    pinned_memory_service.clear_module_state()


class TestConversationMemoryCore(
    BaseLocalMemoryTest, MockMixin, AsyncTestMixin, AssertionMixin
):
    """ConversationMemoryCore 核心功能测试类"""

    @pytest.fixture
    def conversation_memory_core(self, sample_user_data):
        """ConversationMemoryCore实例fixture"""
        config = AgentConfig()
        return ConversationMemoryCore(
            user_id=sample_user_data["user_id"],
            thread_id=sample_user_data["thread_id"],
            agent_config=config,
        )

    @pytest.fixture
    def conversation_memory_core_with_config(self, sample_user_data):
        """带配置的ConversationMemoryCore实例fixture"""
        config = AgentConfig()
        return ConversationMemoryCore(
            user_id=sample_user_data["user_id"],
            thread_id=sample_user_data["thread_id"],
            agent_config=config,
        )

    # ==================== 基础功能测试 ====================

    @pytest.mark.asyncio
    async def test_update_conversation_cache_rolling_trim(
        self, conversation_memory_core
    ):
        """写路径滚动裁剪: 窗口总量恒 <= budget, 超限丢最老轮次.

        回归 fc3e7f78 引入的全量 append 式缓存(无限膨胀) -> 滚动有界窗口.
        """
        from src.agent.memory.local_memory.cache import (
            get_conversation,
            reset_global_cache,
            set_conversation,
        )
        from src.storage.models.conversation import ConversationIndex

        reset_global_cache()
        try:
            uid = conversation_memory_core.user_id
            tid = conversation_memory_core.thread_id
            aid = conversation_memory_core.agent_id
            # 预算 1000: 每轮 300 字符(user/asst 各 150), 3 轮 900 <= 1000, 4 轮 1200 > 1000
            conversation_memory_core.agent_config.memory.total_char_budget = 1000

            half = "x" * 150
            seed = [
                ConversationIndex(
                    round_number=r, user_message=half, assistant_response=half
                )
                for r in (1, 2, 3)
            ]
            set_conversation(uid, tid, seed, agent_id=aid)

            data = create_mock_conversation_data(
                user_id=uid,
                thread_id=tid,
                agent_id=aid,
                round_number=4,
                user_message=half,
                assistant_response=half,
            )
            await conversation_memory_core._update_conversation_cache(data)

            result = get_conversation(uid, tid, agent_id=aid)
            assert isinstance(result, list)
            # 最老的轮 1 被裁掉, 窗口保持 3 轮(总量 900 <= 1000)
            assert [c.round_number for c in result] == [2, 3, 4]
            total = sum(len(c.user_message) + len(c.assistant_response) for c in result)
            assert total <= 1000
        finally:
            reset_global_cache()

    @pytest.mark.asyncio
    async def test_update_conversation_cache_skips_when_unseeded(
        self, conversation_memory_core
    ):
        """缓存未初始化(冷启动由读路径种子化)时写路径应跳过, 不凭空种入单轮."""
        from src.agent.memory.local_memory.cache import (
            get_conversation,
            reset_global_cache,
        )

        reset_global_cache()
        try:
            uid = conversation_memory_core.user_id
            tid = conversation_memory_core.thread_id

            data = create_mock_conversation_data(
                user_id=uid, thread_id=tid, round_number=1
            )
            await conversation_memory_core._update_conversation_cache(data)

            # 未种子化 -> 跳过, 缓存仍为 None(交由读路径冷启动)
            assert get_conversation(uid, tid, agent_id=data.agent_id) is None
        finally:
            reset_global_cache()

    # ==================== 6个并行操作测试 ====================

    @pytest.mark.asyncio
    async def test_add_conversation_round_database_error_handling(
        self, conversation_memory_core, sample_conversation_data
    ):
        """测试并行任务中数据库错误的容错处理"""
        from tests.mocks.service_mock_factory import ServiceMockFactory

        # 使用ServiceMockFactory创建错误场景
        error_service = ServiceMockFactory.create_service_error_scenario(
            error_type="database", service_name="conversation"
        )

        # 创建其他service的mock（避免真实初始化）
        mock_vector_service = AsyncMock()
        mock_conv_data_service = AsyncMock()
        mock_pinned_manager_instance = AsyncMock()
        mock_pinned_manager_instance.get_memory_for_analysis.return_value = (
            "### 基本画像\n(空)\n\n### 口味偏好\n(空)\n"
        )
        mock_analyzer_instance = AsyncMock()
        mock_analyzer_instance.analyze_pinned_memory_update.return_value = (
            PinnedMemoryUpdateResult()
        )
        mock_analyzer = AsyncMock()
        mock_analyzer_instance2 = AsyncMock()
        mock_analyzer_instance2.analyze_conversation_index.return_value = MagicMock(
            topic="测试", summary="测试"
        )
        mock_analyzer.return_value = mock_analyzer_instance2

        # 需要完整mock所有service以避免真实数据库初始化
        with (
            patch(
                "src.agent.memory.local_memory.core.create_conversation_service",
                return_value=error_service,
            ),
            patch(
                "src.agent.memory.local_memory.core.create_vector_service",
                return_value=mock_vector_service,
            ),
            patch(
                "src.agent.memory.local_memory.core.create_conversation_data_service",
                return_value=mock_conv_data_service,
            ),
            patch(
                "src.agent.memory.local_memory.pinned_memory.SimplePinnedMemoryManager",
                return_value=mock_pinned_manager_instance,
            ),
            patch(
                "src.inference.content_analyzer.simple_analyzer.SimpleContentAnalyzer",
                return_value=mock_analyzer_instance,
            ),
            patch(
                "src.inference.content_analyzer.simple_analyzer.get_content_analyzer",
                return_value=mock_analyzer,
            ),
            patch(
                "src.agent.memory.local_memory.pinned_memory_service.create_todo_service",
                return_value=mock_pinned_manager_instance,
            ),
        ):
            # 执行测试 - 并行任务中的错误不会导致整个方法失败
            # 异常会被asyncio.gather捕获并作为结果返回
            await conversation_memory_core.add_conversation_round(
                sample_conversation_data
            )

            # add_conversation_round 正常完成(异常被 asyncio.gather 容错捕获)

    # ==================== 置顶后台化与串行化测试 ====================

    @pytest.mark.timeout(10)
    @pytest.mark.asyncio
    async def test_pinned_update_is_fire_and_forget(
        self, conversation_memory_core, sample_conversation_data
    ):
        """置顶更新转后台: add_conversation_round 不等待 _update_pinned_memory 即返回.

        修复前 _update_pinned_memory 在 gather 内被 await, 阻塞主流程;
        修复后为 fire-and-forget, 主流程不等它.
        """
        conversation_memory_core._embeddings_enabled = False  # 跳过向量存储
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_pinned_update(data):
            entered.set()
            await release.wait()  # 模拟慢 LLM, 永不自行放行

        with (
            patch.object(
                conversation_memory_core._pinned_svc, "update", slow_pinned_update
            ),
            patch("src.agent.memory.local_memory.core.create_conversation_service"),
            patch("src.agent.memory.local_memory.core.create_vector_service"),
            patch(
                "src.agent.memory.local_memory.core.create_conversation_data_service"
            ),
            patch(
                "src.inference.content_analyzer.simple_analyzer.get_content_analyzer"
            ),
        ):
            # release 未 set; 若主流程同步等待置顶更新, wait_for 会超时
            # timeout=8 容忍 xdist 高并发下的 CPU 争用; 慢任务永不放行, 有限值即可抓 bug
            await asyncio.wait_for(
                conversation_memory_core.add_conversation_round(
                    sample_conversation_data
                ),
                timeout=8,
            )

        # 主流程已返回, 但后台置顶任务仍在等 release
        assert entered.is_set(), "后台置顶任务应已启动"
        assert not release.is_set()
        release.set()
        await _drain_pinned_bg_tasks()

    @pytest.mark.timeout(10)
    @pytest.mark.asyncio
    async def test_audit_holding_lock_serializes_pinned_update(
        self, conversation_memory_core, sample_conversation_data
    ):
        """审计持锁期间, 每轮置顶更新在锁上排队, 串行化杜绝 lost update."""
        audit_release = asyncio.Event()
        audit_holding = asyncio.Event()
        update_started = asyncio.Event()
        update_done = asyncio.Event()

        async def blocking_audit(memory_block, number_map, index_block):
            audit_holding.set()
            await audit_release.wait()
            return []

        async def fast_analyze_update(*args, **kwargs):
            return PinnedMemoryUpdateResult()

        with (
            patch(
                "src.inference.content_analyzer.pinned_memory_audit_analyzer.PinnedMemoryAuditAnalyzer"
            ) as mock_audit_cls,
            patch(
                "src.inference.content_analyzer.simple_analyzer.SimpleContentAnalyzer"
            ) as mock_simple_cls,
            patch(
                "src.agent.memory.local_memory.pinned_memory.SimplePinnedMemoryManager"
            ) as mock_pm_cls,
            patch(
                "src.agent.memory.local_memory.core.create_conversation_service",
                new=AsyncMock(),
            ),
            patch("src.config.inference_config.get_config") as mock_cfg,
            patch.object(
                conversation_memory_core._pinned_svc,
                "_get_todo_list",
                new=AsyncMock(return_value=""),
            ),
        ):
            mock_audit_inst = AsyncMock()
            mock_audit_inst.audit = blocking_audit
            mock_audit_cls.return_value = mock_audit_inst

            mock_pm = AsyncMock()
            mock_pm.get_memory_for_audit.return_value = (
                "[1] x",
                {1: {"field": "basic_info", "content": "x"}},
            )
            mock_pm.get_memory_for_analysis.return_value = "block"
            mock_pm.apply_operations.return_value = True
            mock_pm_cls.return_value = mock_pm

            mock_simple_inst = AsyncMock()
            mock_simple_inst.analyze_pinned_memory_update = fast_analyze_update
            mock_simple_cls.return_value = mock_simple_inst

            mock_cfg.return_value = MagicMock()

            # 1. 启动审计 → 持锁, 在 blocking_audit 处阻塞
            audit_task = asyncio.create_task(
                conversation_memory_core._pinned_svc.audit(sample_conversation_data)
            )
            await asyncio.wait_for(audit_holding.wait(), timeout=8)

            # 2. 启动置顶更新 → 应在 _get_pinned_lock 上排队
            async def update_then_signal():
                update_started.set()
                await conversation_memory_core._pinned_svc.update(
                    sample_conversation_data
                )
                update_done.set()

            update_task = asyncio.create_task(update_then_signal())
            await update_started.wait()
            assert not update_done.is_set(), "置顶更新不应在审计持锁期间完成"

            # 3. 放行审计 → 锁释放 → 置顶更新随后完成
            audit_release.set()
            await asyncio.wait_for(update_task, timeout=1.0)
            assert update_done.is_set()

            audit_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await audit_task

    @pytest.mark.timeout(10)
    @pytest.mark.asyncio
    async def test_mark_audited_at_commit_point_prevents_retrigger(
        self, conversation_memory_core
    ):
        """early-mark: round20 触发审计后立即同步 mark, round21 不再重复触发.

        修复前 mark 在审计 finally 才执行, 慢审计期间 round21 也会触发(R20/R21 双发).
        """
        conversation_memory_core._embeddings_enabled = False
        from src.agent.memory.local_memory.pinned_memory_service import _should_audit

        data_r20 = create_mock_conversation_data(
            round_number=20, user_message="m20", assistant_response="r20"
        )

        audit_release = asyncio.Event()

        async def slow_audit(data):
            await audit_release.wait()

        async def noop_update(data):
            return None

        with (
            patch.object(conversation_memory_core._pinned_svc, "audit", slow_audit),
            patch.object(conversation_memory_core._pinned_svc, "update", noop_update),
            patch("src.agent.memory.local_memory.core.create_conversation_service"),
            patch("src.agent.memory.local_memory.core.create_vector_service"),
            patch(
                "src.agent.memory.local_memory.core.create_conversation_data_service"
            ),
            patch(
                "src.inference.content_analyzer.simple_analyzer.get_content_analyzer"
            ),
        ):
            # round20: 触发审计(early-mark 立即把 last 推到 20), 审计任务后台阻塞
            await conversation_memory_core.add_conversation_round(data_r20)

            # round21: 即使 round20 审计仍未完成, 也不应再触发
            uid = conversation_memory_core.user_id
            tid = conversation_memory_core.thread_id
            aid = conversation_memory_core.agent_id
            assert not _should_audit(uid, tid, aid, 21), (
                "round21 不应在 round20 审计未完成时重复触发"
            )

            audit_release.set()
            await _drain_pinned_bg_tasks()


class TestConversationMemoryCoreEmbeddingsConfig(
    BaseLocalMemoryTest, MockMixin, AsyncTestMixin, AssertionMixin
):
    """ConversationMemoryCore 嵌入模型配置测试类"""

    @pytest.fixture
    def conversation_memory_core(self, sample_user_data):
        """ConversationMemoryCore实例fixture"""
        config = AgentConfig()
        return ConversationMemoryCore(
            user_id=sample_user_data["user_id"],
            thread_id=sample_user_data["thread_id"],
            agent_config=config,
        )

    @pytest.mark.asyncio
    async def test_vector_storage_skipped_when_embeddings_disabled(
        self, conversation_memory_core, sample_conversation_data
    ):
        """测试embeddings.enabled=false时跳过向量存储"""
        from tests.mocks.service_mock_factory import ServiceMockFactory

        # 强制设置embeddings.enabled=false
        conversation_memory_core._embeddings_enabled = False

        # 创建Mock服务
        services = ServiceMockFactory.create_all_services()

        with (
            patch(
                "src.agent.memory.local_memory.core.create_conversation_data_service"
            ),
            patch(
                "src.agent.memory.local_memory.core.create_vector_service"
            ) as mock_create_vec,
            patch(
                "src.inference.content_analyzer.simple_analyzer.SimpleContentAnalyzer"
            ),
            patch(
                "src.agent.memory.local_memory.pinned_memory.SimplePinnedMemoryManager"
            ),
            patch(
                "src.inference.content_analyzer.simple_analyzer.get_content_analyzer"
            ),
            patch(
                "src.agent.memory.local_memory.pinned_memory_service.create_todo_service"
            ),
        ):
            # 设置向量服务Mock
            mock_create_vec.return_value = services["vector"]

            # 执行添加对话轮次
            await conversation_memory_core.add_conversation_round(
                sample_conversation_data
            )

            # 验证向量存储服务未被调用
            assert services["vector"].add_conversation_content.call_count == 0

    @pytest.mark.asyncio
    async def test_vector_storage_executed_when_embeddings_enabled(
        self, conversation_memory_core, sample_conversation_data
    ):
        """测试embeddings.enabled=true时执行向量存储"""
        from tests.mocks.service_mock_factory import ServiceMockFactory

        # 确保embeddings.enabled=true（默认值）
        conversation_memory_core._embeddings_enabled = True

        # 创建Mock服务
        services = ServiceMockFactory.create_all_services()

        with (
            patch(
                "src.agent.memory.local_memory.core.create_conversation_data_service"
            ),
            patch(
                "src.agent.memory.local_memory.core.create_vector_service"
            ) as mock_create_vec,
            patch(
                "src.inference.content_analyzer.simple_analyzer.SimpleContentAnalyzer"
            ),
            patch(
                "src.agent.memory.local_memory.pinned_memory.SimplePinnedMemoryManager"
            ),
            patch(
                "src.inference.content_analyzer.simple_analyzer.get_content_analyzer"
            ),
            patch(
                "src.agent.memory.local_memory.pinned_memory_service.create_todo_service"
            ),
        ):
            # 设置向量服务Mock
            mock_create_vec.return_value = services["vector"]

            # 执行添加对话轮次
            await conversation_memory_core.add_conversation_round(
                sample_conversation_data
            )

            # 验证向量存储服务被调用（ConversationMemoryCore._store_vector_conversation）
            assert services["vector"].add_conversation_content.call_count == 1
