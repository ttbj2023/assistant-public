"""MemoryAssembler 单元测试.

测试职责: 验证 messages 数组路径的组装行为
测试范围: assemble_memory_context 端到端 (Mock pinned 获取)
          _build_history_messages 滚动缓存语义 (命中零 DB / 冷启动种子化)
          _find_latest_formatted_suffix_start 纯预算驱动

select_main_history_suffix 的纯函数测试见 test_history_budget.py.

Mock策略: Mock MemoryAssembler 的 pinned 方法,
          Mock create_conversation_service 避免 DB 访问.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agent.memory.local_memory.assembler import (
    MemoryAssembler,
    MemoryContext,
)
from src.agent.memory.local_memory.cache import reset_global_cache, set_conversation
from src.storage.models.conversation import ConversationIndex


@pytest.fixture(autouse=True)
def _reset_memory_cache():
    """每个测试重置全局记忆缓存, 保证读路径从冷启动(缓存空)开始.

    滚动缓存设计下, 命中缓存会跳过 DB mock; 不重置会因跨测试同 (u,t) 缓存泄漏
    导致后续测试命中旧窗口而非走 mock 路径.
    """
    reset_global_cache()
    yield
    reset_global_cache()


def _make_conv(round_number: int, user: str, asst: str) -> ConversationIndex:
    """构造 ConversationIndex (round_number + 内容)."""
    return ConversationIndex(
        round_number=round_number,
        user_message=user,
        assistant_response=asst,
    )


def _make_index_conv(
    round_number: int,
    topic: str | None = None,
    summary: str | None = None,
) -> ConversationIndex:
    """构造索引区 fine 行用的 ConversationIndex (带 topic/summary)."""
    return ConversationIndex(
        round_number=round_number,
        user_message=f"u{round_number}",
        assistant_response=f"a{round_number}",
        topic=topic if topic is not None else f"t{round_number}",
        summary=summary if summary is not None else f"s{round_number}",
    )


class TestAssembleMemoryContext:
    """assemble_memory_context 端到端 (Mock 外部依赖)."""

    @pytest.mark.asyncio
    async def test_first_round_returns_empty_history(self) -> None:
        """无历史时 history_messages 应为空, extension/todo 来自缓存."""
        agent_config = Mock()
        agent_config.memory = Mock()
        agent_config.memory.total_char_budget = 40000

        assembler = MemoryAssembler(
            agent_id="test-agent",
            agent_config=agent_config,
        )

        assembler._get_pinned_memory_with_cache = AsyncMock(return_value="")

        mock_conv_service = AsyncMock()
        mock_conv_service.get_latest_round_number.return_value = 0
        mock_conv_service.conversation_dao = AsyncMock()

        with patch(
            "src.agent.memory.local_memory.assembler.create_conversation_service",
            return_value=mock_conv_service,
        ):
            ctx = await assembler.assemble_memory_context(
                user_id="u",
                thread_id="t",
            )

        assert isinstance(ctx, MemoryContext)
        assert ctx.history_messages == []
        assert ctx.system_prompt_extension == ""

    @pytest.mark.asyncio
    async def test_pinned_memory_wrapped_in_xml_extension(self) -> None:
        """置顶记忆非空时应包裹在 <pinned_memory> XML 标签内."""
        agent_config = Mock()
        agent_config.memory = Mock()
        agent_config.memory.total_char_budget = 40000

        assembler = MemoryAssembler(
            agent_id="test-agent",
            agent_config=agent_config,
        )

        assembler._get_pinned_memory_with_cache = AsyncMock(
            return_value="用户偏好: 早起",
        )

        mock_conv_service = AsyncMock()
        mock_conv_service.get_latest_round_number.return_value = 0

        with patch(
            "src.agent.memory.local_memory.assembler.create_conversation_service",
            return_value=mock_conv_service,
        ):
            ctx = await assembler.assemble_memory_context(
                user_id="u",
                thread_id="t",
            )

        assert "<pinned_memory>" in ctx.system_prompt_extension
        assert "</pinned_memory>" in ctx.system_prompt_extension
        assert "用户偏好: 早起" in ctx.system_prompt_extension

    @pytest.mark.asyncio
    async def test_history_with_index_area_and_main_history(self) -> None:
        """完整场景: 索引区伪对话轮 + 主历史真实 Human/AI 交替."""
        agent_config = Mock()
        agent_config.memory = Mock()
        agent_config.memory.total_char_budget = 1000

        assembler = MemoryAssembler(
            agent_id="test-agent",
            agent_config=agent_config,
        )

        assembler._get_pinned_memory_with_cache = AsyncMock(return_value="")

        mock_conv_service = AsyncMock()
        mock_conv_service.get_latest_round_number.return_value = 5

        main_convs = [
            _make_conv(4, "u4", "a4"),
            _make_conv(5, "u5", "a5"),
        ]

        # get_conversations_in_range 区分: 主历史(end=latest=5) vs 索引区(end=index_end=3)
        async def fake_get_range(start, end, *a, **k):
            if end >= 5:
                return main_convs
            return [_make_index_conv(i) for i in range(start, end + 1)]

        mock_conv_service.get_conversations_in_range = AsyncMock(
            side_effect=fake_get_range,
        )

        mock_conv_service.get_formatted_index_range = AsyncMock(
            return_value="索引摘要",
        )
        mock_conv_service.get_index_groups_up_to = AsyncMock(return_value=[])

        with patch(
            "src.agent.memory.local_memory.assembler.create_conversation_service",
            return_value=mock_conv_service,
        ):
            ctx = await assembler.assemble_memory_context(
                user_id="u",
                thread_id="t",
            )

        assert len(ctx.history_messages) == 6

        assert isinstance(ctx.history_messages[0], HumanMessage)
        assert ctx.history_messages[0].content == "[过往对话回顾]"

        assert isinstance(ctx.history_messages[1], AIMessage)
        assert "<conversation_index>" in ctx.history_messages[1].content
        assert "索引摘要" in ctx.history_messages[1].content

        assert ctx.history_messages[2].content == "u4"
        assert ctx.history_messages[3].content == "a4"
        assert ctx.history_messages[4].content == "u5"
        assert ctx.history_messages[5].content == "a5"

    @pytest.mark.asyncio
    async def test_index_region_covers_all_rounds_when_budget_sufficient(self) -> None:
        """索引区预算足够时应覆盖到 round 1(零丢弃).

        独立预算机制下, 主历史仅装最近若干轮, 其余轮次全靠索引区.
        预算充足时二分查找 hi 倍增到 max_rounds, 最终覆盖起点到 round 1.
        """
        agent_config = Mock()
        agent_config.memory = Mock()
        agent_config.memory.total_char_budget = 20000
        agent_config.memory.index_char_budget = 10000

        assembler = MemoryAssembler(agent_id="test-agent", agent_config=agent_config)
        assembler._get_pinned_memory_with_cache = AsyncMock(return_value="")

        mock_conv_service = AsyncMock()
        mock_conv_service.get_latest_round_number.return_value = 60
        # 主历史仅最近 10 轮 (51-60), 其余轮次交由索引区, index_end = 50

        # get_conversations_in_range 区分: 主历史(end=latest=60) vs 索引区(end=index_end=50)
        async def fake_get_range(start, end, *a, **k):
            if end >= 60:
                return [_make_conv(i, f"u{i}", f"a{i}") for i in range(51, 61)]
            return [_make_index_conv(i) for i in range(start, end + 1)]

        mock_conv_service.get_conversations_in_range = AsyncMock(
            side_effect=fake_get_range,
        )

        requested = []

        async def fake_index_range(uid, tid, start, end, format_template="markdown"):
            requested.append((start, end))
            return f"[{start}-{end}]"

        mock_conv_service.get_formatted_index_range = AsyncMock(
            side_effect=fake_index_range,
        )
        mock_conv_service.get_index_groups_up_to = AsyncMock(return_value=[])

        with patch(
            "src.agent.memory.local_memory.assembler.create_conversation_service",
            return_value=mock_conv_service,
        ):
            ctx = await assembler.assemble_memory_context(
                user_id="u",
                thread_id="t",
            )

        assert "<conversation_index>" in ctx.history_messages[1].content
        assert requested, "索引区应被查询"
        assert min(s for s, _ in requested) == 1


class TestRollingMainHistoryCache:
    """滚动主历史缓存语义: 命中零 DB / 冷启动种子化."""

    def _make_assembler(self, budget: int = 1000) -> MemoryAssembler:
        agent_config = Mock()
        agent_config.memory = Mock()
        agent_config.memory.total_char_budget = budget
        # 关闭索引区, 隔离主历史行为
        agent_config.memory.index_char_budget = 0

        assembler = MemoryAssembler(agent_id="test-agent", agent_config=agent_config)
        assembler._get_pinned_memory_with_cache = AsyncMock(return_value="")
        return assembler

    @pytest.mark.asyncio
    async def test_cold_start_seeds_from_db(self) -> None:
        """缓存空(冷启动): 调 get_conversations_in_range 种子化主历史."""
        assembler = self._make_assembler(budget=1000)

        mock_conv_service = AsyncMock()
        mock_conv_service.get_latest_round_number.return_value = 5
        seeded = [_make_conv(4, "u4", "a4"), _make_conv(5, "u5", "a5")]
        mock_conv_service.get_conversations_in_range = AsyncMock(return_value=seeded)

        with patch(
            "src.agent.memory.local_memory.assembler.create_conversation_service",
            return_value=mock_conv_service,
        ):
            ctx = await assembler.assemble_memory_context(user_id="u", thread_id="t")

        mock_conv_service.get_conversations_in_range.assert_called_once()
        contents = [m.content for m in ctx.history_messages]
        assert "u4" in contents and "u5" in contents

    @pytest.mark.asyncio
    async def test_cache_hit_no_main_history_db_read(self) -> None:
        """缓存已种子化后命中: 不再调 get_conversations_in_range(主历史零 DB).

        索引区读 DB 是初始设计(不在此断言范围), 仅断言主历史路径零 DB.
        """
        assembler = self._make_assembler(budget=1000)
        set_conversation(
            "u",
            "t",
            [_make_conv(4, "u4", "a4"), _make_conv(5, "u5", "a5")],
            agent_id="test-agent",
        )

        mock_conv_service = AsyncMock()
        mock_conv_service.get_latest_round_number.return_value = 5
        mock_conv_service.get_conversations_in_range = AsyncMock(return_value=[])

        with patch(
            "src.agent.memory.local_memory.assembler.create_conversation_service",
            return_value=mock_conv_service,
        ):
            ctx = await assembler.assemble_memory_context(user_id="u", thread_id="t")

        mock_conv_service.get_conversations_in_range.assert_not_called()
        contents = [m.content for m in ctx.history_messages]
        assert "u4" in contents and "u5" in contents

    @pytest.mark.asyncio
    async def test_cache_miss_with_no_rounds_returns_empty(self) -> None:
        """latest_round<=0 时直接返回空, 不触碰对话查询."""
        assembler = self._make_assembler()

        mock_conv_service = AsyncMock()
        mock_conv_service.get_latest_round_number.return_value = 0
        mock_conv_service.get_conversations_in_range = AsyncMock(return_value=[])

        with patch(
            "src.agent.memory.local_memory.assembler.create_conversation_service",
            return_value=mock_conv_service,
        ):
            ctx = await assembler.assemble_memory_context(user_id="u", thread_id="t")

        mock_conv_service.get_conversations_in_range.assert_not_called()
        assert ctx.history_messages == []


class TestFetchIndexInBudget:
    """_fetch_index_in_budget: budget 驱动级联展示(fine 优先, 溢出 group 全弧)."""

    def _make_assembler(self) -> MemoryAssembler:
        assembler = MemoryAssembler.__new__(MemoryAssembler)
        assembler._formatter = Mock()
        return assembler

    def _make_group(self, start: int, end: int, arc: str = "arc") -> Mock:
        g = Mock()
        g.round_start = start
        g.round_end = end
        g.arc_phrase = arc
        return g

    @pytest.mark.asyncio
    async def test_budget_enough_all_fine_no_arc(self) -> None:
        """budget 足够: 全 fine 行, 0 弧短语展示(弧短语冻结但不展示)."""
        assembler = self._make_assembler()
        assembler._formatter.format_index_groups = AsyncMock(return_value="")
        conv_svc = AsyncMock()
        conv_svc.get_conversations_in_range = AsyncMock(
            return_value=[_make_index_conv(i) for i in range(1, 11)],
        )
        conv_svc.get_index_groups_up_to = AsyncMock(
            return_value=[self._make_group(1, 5), self._make_group(6, 10)],
        )
        fine_calls = []

        async def fake_fine(uid, tid, start, end, format_template="markdown"):
            fine_calls.append((start, end))
            return f"<index>{start}-{end}</index>"

        conv_svc.get_formatted_index_range = AsyncMock(side_effect=fake_fine)

        await assembler._fetch_index_in_budget(
            conv_svc, "u", "t", end_round=10, budget=10000,
        )

        # raw_fine_start=1 => arc_groups 空, 不展示弧短语
        assembler._formatter.format_index_groups.assert_not_called()
        assert fine_calls == [(1, 10)]

    @pytest.mark.asyncio
    async def test_budget_tight_recent_fine_plus_arc(self) -> None:
        """budget 不足: 近期 fine + 远期弧短语(group 起点溢出)."""
        assembler = self._make_assembler()
        timeline_calls = []

        async def fake_timeline(groups_data):
            timeline_calls.append(groups_data)
            return "<timeline>"

        assembler._formatter.format_index_groups = AsyncMock(side_effect=fake_timeline)
        conv_svc = AsyncMock()
        conv_svc.get_conversations_in_range = AsyncMock(
            return_value=[_make_index_conv(i, summary="S" * 40) for i in range(1, 11)],
        )
        conv_svc.get_index_groups_up_to = AsyncMock(
            return_value=[self._make_group(1, 3), self._make_group(4, 5)],
        )
        fine_calls = []

        async def fake_fine(uid, tid, start, end, format_template="markdown"):
            fine_calls.append((start, end))
            return f"<index>{start}-{end}</index>"

        conv_svc.get_formatted_index_range = AsyncMock(side_effect=fake_fine)

        await assembler._fetch_index_in_budget(
            conv_svc, "u", "t", end_round=10, budget=200,
        )

        # 有弧短语展示(两个 group 起点都溢出)
        arc_starts = [g["round_start"] for groups in timeline_calls for g in groups]
        assert 1 in arc_starts and 4 in arc_starts
        # fine 区被裁(start>1)且在 end_round 内
        assert fine_calls[0][0] > 1
        assert fine_calls[0][1] == 10

    @pytest.mark.asyncio
    async def test_straddling_group_shows_full_arc(self) -> None:
        """跨界 group 全弧展示, fine 从其 round_end+1 起(不拆分, 避免重复)."""
        assembler = self._make_assembler()
        timeline_calls = []

        async def fake_timeline(groups_data):
            timeline_calls.append(groups_data)
            return "<timeline>"

        assembler._formatter.format_index_groups = AsyncMock(side_effect=fake_timeline)
        conv_svc = AsyncMock()
        conv_svc.get_conversations_in_range = AsyncMock(
            return_value=[_make_index_conv(i, summary="S" * 40) for i in range(1, 11)],
        )
        # group(4-7) 将跨界: raw_fine_start 落在其范围内
        conv_svc.get_index_groups_up_to = AsyncMock(
            return_value=[self._make_group(1, 3), self._make_group(4, 7)],
        )
        fine_calls = []

        async def fake_fine(uid, tid, start, end, format_template="markdown"):
            fine_calls.append((start, end))
            return f"<index>{start}-{end}</index>"

        conv_svc.get_formatted_index_range = AsyncMock(side_effect=fake_fine)

        # budget 使 raw_fine_start 落在 group(4-7) 内(每行~60字符, [6,10]~310<=330)
        await assembler._fetch_index_in_budget(
            conv_svc, "u", "t", end_round=10, budget=330,
        )

        # 跨界 group(4-7) 全弧展示
        arc_starts = [g["round_start"] for groups in timeline_calls for g in groups]
        assert 4 in arc_starts
        # fine 从 group(4-7) round_end+1 = 8 起(跨界推高, 非裸 raw_fine_start)
        assert fine_calls[0][0] == 8, f"fine 应从 8 起(跨界 group 末尾+1), 实际 {fine_calls}"
