"""VectorBackfillService 单元测试 - 向量库缺失轮次的懒补偿.

覆盖核心场景:
- diff 补偿: SQL 有 N 轮 / 向量库 M 轮 (M<N) → 补入缺失
- 单轮失败隔离: 某轮补入失败不阻断后续轮次 (对标 Ollama 503 全程失败)
- 幂等: 向量库已齐全时不重复补
- embeddings 关闭 / 客户端创建失败 → 跳过
- frontier 游标: 补完后推进, 避免重复扫描
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.memory.local_memory import vector_backfill_service
from src.agent.memory.local_memory.vector_backfill_service import (
    VectorBackfillService,
)


def _conv(
    round_number: int, user_message: str = "u", assistant: str = "a"
) -> MagicMock:
    """构造 ConversationIndex mock."""
    m = MagicMock()
    m.round_number = round_number
    m.user_message = user_message
    m.assistant_response = assistant
    m.summary = "summary"
    return m


def _build_service(embeddings: Any = None) -> VectorBackfillService:
    """构造已注入 embeddings 的补偿服务(跳过惰性创建)."""
    svc = VectorBackfillService("u", "t", "agent")
    if embeddings is not None:
        svc._embeddings = embeddings
    return svc


def _data(round_number: int) -> MagicMock:
    """构造 ConversationData mock."""
    m = MagicMock()
    m.round_number = round_number
    return m


@contextmanager
def _backfill_env(
    conv_svc: Any,
    vector_store: Any,
    enabled: bool = True,
) -> Iterator[None]:
    """统一 patch: embeddings 开关 + conversation/vector 工厂."""
    with (
        patch(
            "src.agent.memory.local_memory.vector_backfill_service._embeddings_enabled",
            return_value=enabled,
        ),
        patch(
            "src.agent.memory.local_memory.vector_backfill_service.create_conversation_service",
            new=AsyncMock(return_value=conv_svc),
        ),
        patch(
            "src.agent.memory.local_memory.vector_backfill_service.create_langchain_vector_store",
            return_value=vector_store,
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def _reset_module() -> Iterator[None]:
    vector_backfill_service.clear_module_state()
    yield
    vector_backfill_service.clear_module_state()


def _added_rounds(vs: MagicMock) -> list[int]:
    """提取 add_conversation_round 被调用的 round_number 序列."""
    return [c.kwargs["round_number"] for c in vs.add_conversation_round.call_args_list]


class TestVectorBackfillDiff:
    """diff 补偿核心逻辑."""

    @pytest.mark.asyncio
    async def test_should_backfill_missing_rounds(self):
        """SQL有3轮/向量库1轮 → 应补入缺失的 round 2,3(保留已有的 round 1)."""
        conv_svc = MagicMock()
        conv_svc.get_conversations_in_range = AsyncMock(
            return_value=[_conv(1), _conv(2), _conv(3)],
        )
        vs = MagicMock()
        vs.get_existing_round_numbers = AsyncMock(return_value={1})
        vs.add_conversation_round = AsyncMock(return_value="doc")

        svc = _build_service(embeddings=MagicMock())

        with _backfill_env(conv_svc, vs):
            await svc._backfill_round(_data(3))

        assert _added_rounds(vs) == [2, 3]

    @pytest.mark.asyncio
    async def test_single_round_failure_should_not_block_others(self):
        """单轮补入失败不应阻断后续轮次(对标本次 Ollama 503 全程失败场景)."""
        conv_svc = MagicMock()
        conv_svc.get_conversations_in_range = AsyncMock(
            return_value=[_conv(1), _conv(2), _conv(3)],
        )
        vs = MagicMock()
        vs.get_existing_round_numbers = AsyncMock(return_value=set())
        vs.add_conversation_round = AsyncMock(
            side_effect=[RuntimeError("503"), "doc2", "doc3"],
        )

        svc = _build_service(embeddings=MagicMock())

        with _backfill_env(conv_svc, vs):
            await svc._backfill_round(_data(3))

        # 三轮都被尝试, round 1 失败但 round 2/3 仍补入
        assert vs.add_conversation_round.call_count == 3

    @pytest.mark.asyncio
    async def test_should_skip_when_all_rounds_exist(self):
        """幂等: 向量库已齐全时不调用 add."""
        conv_svc = MagicMock()
        conv_svc.get_conversations_in_range = AsyncMock(
            return_value=[_conv(1), _conv(2)],
        )
        vs = MagicMock()
        vs.get_existing_round_numbers = AsyncMock(return_value={1, 2})
        vs.add_conversation_round = AsyncMock()

        svc = _build_service(embeddings=MagicMock())

        with _backfill_env(conv_svc, vs):
            await svc._backfill_round(_data(2))

        vs.add_conversation_round.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_skip_when_no_sql_rounds(self):
        """SQL 范围内无轮次(空 gap)时跳过."""
        conv_svc = MagicMock()
        conv_svc.get_conversations_in_range = AsyncMock(return_value=[])
        vs = MagicMock()
        vs.get_existing_round_numbers = AsyncMock(return_value=set())

        svc = _build_service(embeddings=MagicMock())

        with _backfill_env(conv_svc, vs):
            await svc._backfill_round(_data(1))

        vs.add_conversation_round.assert_not_called()


class TestVectorBackfillGuard:
    """前置守卫: 跳过场景."""

    @pytest.mark.asyncio
    async def test_should_skip_when_embeddings_disabled(self):
        """embeddings.enabled=false 时跳过补偿."""
        conv_svc = MagicMock()
        vs = MagicMock()
        svc = _build_service(embeddings=MagicMock())

        with _backfill_env(conv_svc, vs, enabled=False):
            await svc._backfill_round(_data(1))

        vs.get_existing_round_numbers.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_skip_when_embeddings_client_none(self):
        """embedding 客户端创建失败(惰性创建返回 None)时跳过."""
        conv_svc = MagicMock()
        vs = MagicMock()
        svc = _build_service(embeddings=None)  # None 模拟创建失败

        with _backfill_env(conv_svc, vs):
            await svc._backfill_round(_data(1))

        vs.get_existing_round_numbers.assert_not_called()


class TestVectorBackfillFrontier:
    """frontier 游标推进."""

    @pytest.mark.asyncio
    async def test_frontier_should_advance_to_continuous_max(self):
        """补完后 frontier 应推进到连续完整的最大 round."""
        conv_svc = MagicMock()
        conv_svc.get_conversations_in_range = AsyncMock(
            return_value=[_conv(1), _conv(2), _conv(3)],
        )
        vs = MagicMock()
        vs.get_existing_round_numbers = AsyncMock(return_value={1, 2, 3})
        vs.add_conversation_round = AsyncMock()

        svc = _build_service(embeddings=MagicMock())

        with _backfill_env(conv_svc, vs):
            await svc._backfill_round(_data(3))

        assert vector_backfill_service._frontiers["u:t:agent"] == 3

    @pytest.mark.asyncio
    async def test_frontier_should_stop_at_first_gap(self):
        """存在缺口时 frontier 停在缺口前(不越过未补成功的轮)."""
        conv_svc = MagicMock()
        conv_svc.get_conversations_in_range = AsyncMock(
            return_value=[_conv(1), _conv(2), _conv(3), _conv(4)],
        )
        vs = MagicMock()
        # 向量库缺 round 2 (round 3,4 存在, 非连续)
        vs.get_existing_round_numbers = AsyncMock(return_value={1, 3, 4})
        # round 2 补入持续失败
        vs.add_conversation_round = AsyncMock(side_effect=RuntimeError("fail"))

        svc = _build_service(embeddings=MagicMock())

        with _backfill_env(conv_svc, vs):
            await svc._backfill_round(_data(4))

        # frontier 停在 round 1 (round 2 缺口)
        assert vector_backfill_service._frontiers["u:t:agent"] == 1

    @pytest.mark.asyncio
    async def test_frontier_should_limit_sql_scan_range(self):
        """frontier 推进后, 下次 SQL 查询应从 frontier+1 起(避免重复全扫)."""
        conv_svc = MagicMock()
        conv_svc.get_conversations_in_range = AsyncMock(
            return_value=[_conv(3)],
        )
        vs = MagicMock()
        vs.get_existing_round_numbers = AsyncMock(return_value={1, 2, 3})
        vs.add_conversation_round = AsyncMock()

        svc = _build_service(embeddings=MagicMock())
        # 预置 frontier=2 (round 1,2 已确认完整)
        vector_backfill_service._frontiers["u:t:agent"] = 2

        with _backfill_env(conv_svc, vs):
            await svc._backfill_round(_data(3))

        # SQL 查询范围应从 3 起 (frontier+1)
        call = conv_svc.get_conversations_in_range.call_args
        assert call.args[0] == 3  # start_round = frontier+1
        assert call.args[1] == 3  # end_round = cur_round
