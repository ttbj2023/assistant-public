"""IndexRunService 单元测试 - 语义 run 检测与弧短语冻结.

覆盖核心生命周期:
- 相似轮 → 并入 run(不冻结)
- 话题切换 → 闭合 run; 达到 MIN_RUN_SIZE 则冻结弧短语
- 单轮 run 不冻结(留 fine bridge)
- embedding 关闭 → 跳过
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.memory.local_memory import index_run_service
from src.agent.memory.local_memory.index_run_service import (
    IndexRunService,
    detect_runs,
)


class _StubEmbeddings:
    """按 summary 关键词返回正交/平行向量的 stub embedding."""

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    def embed_query(self, text: str) -> list[float]:
        return self._mapping.get(text, [0.0, 0.0])

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


def _conv(round_number: int, summary: str) -> MagicMock:
    m = MagicMock()
    m.round_number = round_number
    m.topic = summary
    m.summary = summary
    return m


def _group(round_start: int, round_end: int) -> MagicMock:
    """构造已冻结的 index group mock(供懒补偿测试预置 frontier)."""
    g = MagicMock()
    g.round_start = round_start
    g.round_end = round_end
    return g


def _build_service(
    threshold: float = 0.5,
    embeddings: Any = None,
    analyzer: Any = None,
) -> IndexRunService:
    svc = IndexRunService("u", "t", "a", similarity_threshold=threshold)
    if embeddings is not None:
        svc._embeddings = embeddings
    if analyzer is not None:
        svc._analyzer = analyzer
    return svc


@contextmanager
def _detection_env(conv_svc: Any, enabled: bool = True) -> Iterator[None]:
    """统一 patch: embeddings 开关 + create_conversation_service 返回 mock."""
    with (
        patch(
            "src.agent.memory.local_memory.index_run_service._embeddings_enabled",
            return_value=enabled,
        ),
        patch(
            "src.agent.memory.local_memory.index_run_service.create_conversation_service",
            new=AsyncMock(return_value=conv_svc),
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def _reset_module() -> Iterator[None]:
    index_run_service.clear_module_state()
    yield
    index_run_service.clear_module_state()


async def _round(svc: IndexRunService, n: int) -> None:
    data = MagicMock()
    data.round_number = n
    await svc._process_round(data)


class TestDetectRunsBatch:
    """detect_runs 纯函数: 与在线服务同语义的批量检测."""

    def _entry(self, rnd: int, summary: str, vec: list[float]) -> dict:
        return {"round": rnd, "topic": summary, "summary": summary, "emb": vec}

    def test_similar_extend_single_run(self) -> None:
        entries = [self._entry(r, "A", [1.0, 0.0]) for r in (1, 2, 3)]
        runs = detect_runs(entries, threshold=0.5)
        assert runs == []  # 末尾 run 未闭合, 不返回

    def test_topic_shift_splits(self) -> None:
        entries = [
            self._entry(1, "A", [1.0, 0.0]),
            self._entry(2, "B", [0.0, 1.0]),
            self._entry(3, "B", [0.0, 1.0]),
            self._entry(4, "C", [1.0, 0.0]),
        ]
        runs = detect_runs(entries, threshold=0.5)
        # min_run_size=1: [1,1] 与 [2,3] 均闭合保留; [4,4] 末尾未闭合不返回
        assert len(runs) == 2
        assert runs[0]["start"] == 1 and runs[0]["end"] == 1
        assert runs[1]["start"] == 2 and runs[1]["end"] == 3
        assert runs[1]["close_sim"] < 0.5

    def test_single_round_run_kept(self) -> None:
        entries = [
            self._entry(1, "A", [1.0, 0.0]),
            self._entry(2, "B", [0.0, 1.0]),
            self._entry(3, "C", [1.0, 1.0]),
        ]
        runs = detect_runs(entries, threshold=0.5)
        # [1,1] 单轮闭合保留(min_run_size=1, 保证无洞); [2,3] B,C 相似并入, 末尾未闭合不返回
        assert len(runs) == 1
        assert runs[0]["start"] == 1 and runs[0]["end"] == 1


class TestRunDetection:
    @pytest.mark.asyncio
    async def test_similar_rounds_extend_no_freeze(self) -> None:
        """连续同主题轮并入 run, 不冻结."""
        emb = _StubEmbeddings({"A": [1.0, 0.0]})
        conv_svc = MagicMock()
        conv_svc.conversation_dao.get_by_round_number = AsyncMock(
            side_effect=lambda r, *a, **k: _conv(r, "A"),
        )
        conv_svc.create_index_group = AsyncMock()
        conv_svc.get_index_groups_up_to = AsyncMock(return_value=[])
        analyzer = MagicMock()
        analyzer.distill = AsyncMock(return_value="arc")

        svc = _build_service(embeddings=emb, analyzer=analyzer)
        with _detection_env(conv_svc):
            for r in (1, 2, 3):
                await _round(svc, r)

        conv_svc.create_index_group.assert_not_called()

    @pytest.mark.asyncio
    async def test_topic_shift_freezes_run(self) -> None:
        """A, B, B, C 序列: 切换处闭合 [1,1] 与 [2,3] 均冻结(min_run_size=1)."""
        emb = _StubEmbeddings({"A": [1.0, 0.0], "B": [0.0, 1.0], "C": [1.0, 0.0]})
        conv_svc = MagicMock()
        summary_by_round = {1: "A", 2: "B", 3: "B", 4: "C"}
        conv_svc.conversation_dao.get_by_round_number = AsyncMock(
            side_effect=lambda r, *a, **k: _conv(r, summary_by_round[r]),
        )
        conv_svc.conversation_dao.get_conversations_in_range = AsyncMock(
            side_effect=lambda s, e, *a, **k: [
                _conv(i, summary_by_round[i]) for i in range(s, e + 1)
            ],
        )
        conv_svc.create_index_group = AsyncMock()
        conv_svc.get_index_groups_up_to = AsyncMock(return_value=[])
        analyzer = MagicMock()
        analyzer.distill = AsyncMock(return_value="饮食记录弧")

        svc = _build_service(embeddings=emb, analyzer=analyzer)
        with _detection_env(conv_svc):
            for r in (1, 2, 3, 4):
                await _round(svc, r)

        # [1,1] 与 [2,3] 均冻结; [4,4] 末尾未闭合不冻结
        assert conv_svc.create_index_group.await_count == 2
        calls = conv_svc.create_index_group.await_args_list
        assert calls[0].kwargs["round_start"] == 1
        assert calls[0].kwargs["round_end"] == 1
        assert calls[1].kwargs["round_start"] == 2
        assert calls[1].kwargs["round_end"] == 3
        assert calls[1].kwargs["arc_phrase"] == "饮食记录弧"

    @pytest.mark.asyncio
    async def test_single_round_run_frozen(self) -> None:
        """A, B 序列: [1,1] 单轮 run 也冻结(min_run_size=1, 保证无洞)."""
        emb = _StubEmbeddings({"A": [1.0, 0.0], "B": [0.0, 1.0]})
        conv_svc = MagicMock()
        summary_by_round = {1: "A", 2: "B"}
        conv_svc.conversation_dao.get_by_round_number = AsyncMock(
            side_effect=lambda r, *a, **k: _conv(r, summary_by_round[r]),
        )
        conv_svc.conversation_dao.get_conversations_in_range = AsyncMock(
            return_value=[_conv(1, "A")],
        )
        conv_svc.create_index_group = AsyncMock()
        conv_svc.get_index_groups_up_to = AsyncMock(return_value=[])
        analyzer = MagicMock()
        analyzer.distill = AsyncMock(return_value="弧")

        svc = _build_service(embeddings=emb, analyzer=analyzer)
        with _detection_env(conv_svc):
            for r in (1, 2):
                await _round(svc, r)

        # r2 闭合时 r1 的单轮 run [1,1] 被冻结
        conv_svc.create_index_group.assert_awaited_once()
        call = conv_svc.create_index_group.await_args
        assert call.kwargs["round_start"] == 1
        assert call.kwargs["round_end"] == 1


class TestEmbeddingsDisabled:
    @pytest.mark.asyncio
    async def test_embeddings_disabled_skips(self) -> None:
        """embedding 关闭时完全跳过 run 检测."""
        conv_svc = MagicMock()
        conv_svc.conversation_dao.get_by_round_number = AsyncMock()
        svc = _build_service()

        with _detection_env(conv_svc, enabled=False):
            await _round(svc, 1)

        conv_svc.conversation_dao.get_by_round_number.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_summary_skips_round(self) -> None:
        """本轮 summary 尚未落库(get_by_round_number 返回 None)时跳过, 不破坏 open run."""
        emb = _StubEmbeddings({"A": [1.0, 0.0]})
        conv_svc = MagicMock()
        conv_svc.conversation_dao.get_by_round_number = AsyncMock(return_value=None)
        conv_svc.create_index_group = AsyncMock()
        conv_svc.get_index_groups_up_to = AsyncMock(return_value=[])
        analyzer = MagicMock()

        svc = _build_service(embeddings=emb, analyzer=analyzer)
        with _detection_env(conv_svc):
            await _round(svc, 1)

        conv_svc.create_index_group.assert_not_called()


class TestLazyCompensation:
    """启动/重启后懒补偿: 冻结 frontier..cur_round-1 间未冻结 gap, 消除丢洞."""

    @pytest.mark.asyncio
    async def test_restart_compensates_gap(self) -> None:
        """重启后 open_run 丢失, 首轮触发补冻结 frontier 之后的 gap 轮次."""
        emb = _StubEmbeddings({"B": [0.0, 1.0], "C": [1.0, 0.0]})
        conv_svc = MagicMock()
        # r6,r7=B(重启前未闭合, 未冻结的 gap); r8=C(触发补偿的新轮)
        summary_by_round = {6: "B", 7: "B", 8: "C"}
        conv_svc.conversation_dao.get_by_round_number = AsyncMock(
            side_effect=lambda r, *a, **k: _conv(r, summary_by_round[r]),
        )
        conv_svc.conversation_dao.get_conversations_in_range = AsyncMock(
            return_value=[_conv(6, "B"), _conv(7, "B")],
        )
        # 预置: group 已覆盖 [1,5](重启前的 frontier)
        conv_svc.get_index_groups_up_to = AsyncMock(return_value=[_group(1, 5)])
        conv_svc.create_index_group = AsyncMock()
        analyzer = MagicMock()
        analyzer.distill = AsyncMock(return_value="gap弧")

        svc = _build_service(embeddings=emb, analyzer=analyzer)
        with _detection_env(conv_svc):
            await _round(svc, 8)

        # gap [6,7] 被补冻结
        conv_svc.create_index_group.assert_awaited_once()
        call = conv_svc.create_index_group.await_args
        assert call.kwargs["round_start"] == 6
        assert call.kwargs["round_end"] == 7

    @pytest.mark.asyncio
    async def test_no_gap_no_compensation(self) -> None:
        """frontier 紧接 cur_round 时无 gap, 不触发补偿(亦覆盖幂等)."""
        emb = _StubEmbeddings({"A": [1.0, 0.0]})
        conv_svc = MagicMock()
        conv_svc.conversation_dao.get_by_round_number = AsyncMock(
            side_effect=lambda r, *a, **k: _conv(r, "A"),
        )
        conv_svc.create_index_group = AsyncMock()
        # frontier=7, cur_round=8, gap=[8,7] 空
        conv_svc.get_index_groups_up_to = AsyncMock(return_value=[_group(1, 7)])
        analyzer = MagicMock()

        svc = _build_service(embeddings=emb, analyzer=analyzer)
        with _detection_env(conv_svc):
            await _round(svc, 8)

        conv_svc.create_index_group.assert_not_called()

    @pytest.mark.asyncio
    async def test_compensation_skips_empty_summary(self) -> None:
        """gap 内空 summary 轮被跳过(无法蒸馏, 同 backfill 约定)."""
        emb = _StubEmbeddings({"B": [0.0, 1.0], "C": [1.0, 0.0]})
        conv_svc = MagicMock()
        # r6 summary 空; r7=B; r8=C(触发轮)
        conv_svc.conversation_dao.get_by_round_number = AsyncMock(
            side_effect=lambda r, *a, **k: _conv(r, "C"),
        )
        empty_r6 = _conv(6, "")
        empty_r6.summary = ""
        conv_svc.conversation_dao.get_conversations_in_range = AsyncMock(
            return_value=[empty_r6, _conv(7, "B")],
        )
        conv_svc.get_index_groups_up_to = AsyncMock(return_value=[_group(1, 5)])
        conv_svc.create_index_group = AsyncMock()
        analyzer = MagicMock()
        analyzer.distill = AsyncMock(return_value="弧")

        svc = _build_service(embeddings=emb, analyzer=analyzer)
        with _detection_env(conv_svc):
            await _round(svc, 8)

        # 仅 r7(空 summary 的 r6 被过滤), 单轮冻结 [7,7]
        conv_svc.create_index_group.assert_awaited_once()
        call = conv_svc.create_index_group.await_args
        assert call.kwargs["round_start"] == 7
        assert call.kwargs["round_end"] == 7
