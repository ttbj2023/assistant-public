"""IndexRunService - 索引区语义 run 检测与弧短语冻结.

每轮对话后(fire-and-forget): 用 summary embedding 判相邻轮主题连续性, 连续
同主题轮构成一个 run; 话题切换(cosine < 阈值)时闭合 run, LLM 蒸馏一句弧短语
冻结写入 conversation_index_group, 永不再压缩.

弧短语兼检索钩子(语义线索)与时间连续性(按序拼接 = 早期对话演变轨迹).
始终用各轮已有 summary 做判定与蒸馏, 不读原文.

拥有独立模块级状态: RMW 串行化锁,未闭合 run 跟踪,fire-and-forget 后台任务.
进程重启会丢失未闭合 run(其轮次留在 fine 区作 bridge, 可接受; 仿 pinned audit).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.storage.service import create_conversation_service

from .semantic_dedup import cosine_similarity

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings

    from src.storage.models.conversation import ConversationData

logger = logging.getLogger(__name__)

# 默认压缩旋钮(可由 agent_config.memory 覆盖, 见 config 集成)
_DEFAULT_SIMILARITY_THRESHOLD = 0.5
_DEFAULT_ARC_MAX_CHARS = 60
# 单轮 run 也冻结: 保证索引区时间线完整无洞(对齐 backfill 语义)
_MIN_RUN_SIZE = 1

# 未闭合 run 跟踪: key -> {start, last, emb}
_open_runs: dict[str, dict[str, Any]] = {}
# RMW 串行化锁: 按 user:thread:agent 索引, 模块级跨实例共享
_index_run_locks: dict[str, asyncio.Lock] = {}
# 存活后台任务引用: 防 fire-and-forget task 被提前 GC
_index_run_bg_tasks: set[asyncio.Task[None]] = set()


def _run_key(user_id: str, thread_id: str, agent_id: str) -> str:
    return f"{user_id}:{thread_id}:{agent_id}"


def _get_index_run_lock(user_id: str, thread_id: str, agent_id: str) -> asyncio.Lock:
    """获取 run 检测 RMW 锁(按 user:thread:agent 索引, lazy 创建)."""
    key = _run_key(user_id, thread_id, agent_id)
    lock = _index_run_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _index_run_locks[key] = lock
    return lock


def _spawn_index_run_bg_task(coro: Any) -> None:
    """启动 run 检测后台任务(fire-and-forget)并登记引用防 GC."""
    task = asyncio.create_task(coro)  # type: ignore[arg-type]
    _index_run_bg_tasks.add(task)
    task.add_done_callback(_index_run_bg_tasks.discard)


def clear_module_state() -> None:
    """清理模块级状态(供测试 fixture 使用)."""
    _index_run_locks.clear()
    _index_run_bg_tasks.clear()
    _open_runs.clear()


def detect_runs(
    entries: list[dict[str, Any]],
    threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
    min_run_size: int = _MIN_RUN_SIZE,
) -> list[dict[str, Any]]:
    """批量检测语义 run(离线评估与在线服务共用同一语义).

    逻辑与 IndexRunService._detect_and_maybe_freeze 逐行对应: 相邻轮 summary
    embedding 余弦 >= threshold 并入同一 run, 否则闭合; 闭合 run 长度 <
    min_run_size 的丢弃(单轮 run 留近期 fine bridge). 末尾未闭合 run 不返回.

    Args:
        entries: [{round, topic, summary, emb}, ...] 按轮次升序
        threshold: 相似度阈值
        min_run_size: 最小冻结 run 长度

    Returns:
        [{start, end, entries, close_sim}, ...] 闭合且达 min_run_size 的 run

    """
    runs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for e in entries:
        emb = e.get("emb") or []
        if current is None:
            current = {"start": e["round"], "emb": emb, "entries": [e]}
            continue
        sim = cosine_similarity(emb, current["emb"])
        if sim >= threshold:
            current["entries"].append(e)
            current["emb"] = emb
        else:
            _close_run_batch(runs, current, min_run_size, sim)
            current = {"start": e["round"], "emb": emb, "entries": [e]}
    return runs


def _close_run_batch(
    runs: list[dict[str, Any]],
    current: dict[str, Any],
    min_run_size: int,
    close_sim: float,
) -> None:
    """把已闭合 run(达 min_run_size) 收进结果, 记录闭合处相似度."""
    if len(current["entries"]) >= min_run_size:
        runs.append({
            "start": current["start"],
            "end": current["entries"][-1]["round"],
            "entries": current["entries"],
            "close_sim": close_sim,
        })


def detect_runs_full_coverage(
    entries: list[dict[str, Any]],
    threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
) -> list[dict[str, Any]]:
    """检测 run 并强制闭合尾部 + 保留单轮 run, 保证覆盖 [1..N] 无 gap.

    backfill 与在线懒补偿共用: gap 数据需完整覆盖(冻结后 frontier 连续无洞),
    故与在线 detect_runs 多一点: 强制闭合尾部 open run(末轮 N 必在 group 内).
    min_run_size 恒为 1(不丢单轮 run, 否则中间单轮轮次会成 gap).

    Args:
        entries: [{round, topic, summary, emb}, ...] 按轮次升序
        threshold: 相似度阈值

    Returns:
        [{start, end, entries, close_sim}, ...] 全覆盖的 run 列表

    """
    runs = detect_runs(entries, threshold=threshold, min_run_size=1)
    if not entries:
        return runs
    last_end = runs[-1]["end"] if runs else 0
    last_round = entries[-1]["round"]
    if last_round > last_end:
        tail = [e for e in entries if e["round"] > last_end]
        if tail:
            runs.append({
                "start": tail[0]["round"],
                "end": tail[-1]["round"],
                "entries": tail,
                "close_sim": None,
            })
    return runs


def get_bg_tasks() -> set[asyncio.Task[None]]:
    """获取存活后台任务集合(供测试 drain 使用)."""
    return _index_run_bg_tasks


def _embeddings_enabled() -> bool:
    """embedding 关闭时跳过 run 检测(无分组, 索引区全 fine bridge)."""
    try:
        from src.config.inference_config import get_config

        return bool(get_config().embeddings.enabled)
    except Exception as e:
        logger.debug("读取 embedding 配置失败, 默认启用: %s", e)
        return True


class IndexRunService:
    """索引 run 检测服务 - 语义连续性判定 + 弧短语冻结.

    每轮对话后(fire-and-forget, RMW 串行):
    - 取本轮 summary 的 embedding, 与未闭合 run 末轮 embedding 比 cosine
    - >= 阈值: 并入当前 run
    - <  阈值: 闭合当前 run(蒸馏弧短语冻结) + 开新 run
    """

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
        similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
        arc_max_chars: int = _DEFAULT_ARC_MAX_CHARS,
    ) -> None:
        self.user_id = user_id
        self.thread_id = thread_id
        self.agent_id = agent_id
        self.similarity_threshold = similarity_threshold
        self.arc_max_chars = arc_max_chars
        self._embeddings: Embeddings | None = None
        self._analyzer: Any = None

    def on_conversation_round(self, conversation_data: ConversationData) -> None:
        """每轮对话后的 run 检测入口(fire-and-forget).

        由 ConversationMemoryCore.add_conversation_round 在并行存储完成后调用
        (此时本轮 topic+summary 已落库).
        """
        _spawn_index_run_bg_task(self._process_round(conversation_data))

    def _get_embeddings(self) -> Embeddings | None:
        """惰性创建 embedding 客户端."""
        if self._embeddings is None:
            try:
                from src.inference.embeddings.embeddings import create_embeddings

                self._embeddings = create_embeddings()
            except Exception as e:
                logger.warning("创建 embedding 客户端失败, 跳过 run 检测: %s", e)
                return None
        return self._embeddings

    def _get_analyzer(self) -> Any:
        """惰性创建弧短语分析器(复用 content_analyzer 模型基座)."""
        if self._analyzer is None:
            from src.config.inference_config import get_config as get_inference_config
            from src.inference.content_analyzer.index_arc_analyzer import (
                IndexArcAnalyzer,
            )

            ca = get_inference_config().content_analyzer
            self._analyzer = IndexArcAnalyzer(
                model_id=ca.arc_model or ca.model,
                model_params=ca.arc_model_params,
                max_chars=self.arc_max_chars,
            )
        return self._analyzer

    async def _process_round(self, conversation_data: ConversationData) -> None:
        """单轮 run 检测(RMW 锁内)."""
        if not _embeddings_enabled():
            return

        lock = _get_index_run_lock(self.user_id, self.thread_id, self.agent_id)
        await lock.acquire()
        try:
            await self._detect_and_maybe_freeze(conversation_data)
        except Exception as e:
            logger.warning(
                "索引 run 检测失败 round %d(不影响主流程): %s",
                conversation_data.round_number,
                e,
            )
        finally:
            lock.release()

    async def _detect_and_maybe_freeze(
        self,
        conversation_data: ConversationData,
    ) -> None:
        """核心: 取本轮 summary embedding, 判连续性, 必要时闭合冻结."""
        embeddings = self._get_embeddings()
        if embeddings is None:
            return

        conv_service = await create_conversation_service(
            self.user_id,
            self.thread_id,
            agent_id=self.agent_id,
        )
        cur_round = conversation_data.round_number
        cur_conv = await conv_service.conversation_dao.get_by_round_number(
            cur_round,
            self.user_id,
            self.thread_id,
        )
        if cur_conv is None or not (cur_conv.summary or "").strip():
            logger.debug("本轮 summary 尚未落库或为空, 跳过: round %d", cur_round)
            return

        try:
            cur_emb = await embeddings.aembed_query(cur_conv.summary or "")
        except Exception as e:
            logger.warning("本轮 summary embedding 失败, 跳过: %s", e)
            return

        key = _run_key(self.user_id, self.thread_id, self.agent_id)
        open_run = _open_runs.get(key)

        if open_run is None:
            # 懒补偿: 重启/首次, 冻结 frontier..cur_round-1 间未冻结 gap, 消除丢洞
            await self._compensate_gap_before(conv_service, cur_round)
            _open_runs[key] = {"start": cur_round, "last": cur_round, "emb": cur_emb}
            logger.debug("开新 run: round %d", cur_round)
            return

        sim = cosine_similarity(cur_emb, open_run["emb"])
        if sim >= self.similarity_threshold:
            open_run["last"] = cur_round
            open_run["emb"] = cur_emb
            logger.debug("并入 run: round %d (sim=%.3f)", cur_round, sim)
            return

        await self._close_run(conv_service, open_run, cur_round, cur_emb)

    async def _compensate_gap_before(
        self,
        conv_service: Any,
        cur_round: int,
    ) -> None:
        """启动/重启后懒补偿: 冻结 frontier..cur_round-1 间未冻结 gap, 消除丢洞.

        重启后 _open_runs 丢失, 之前未闭合 run 的轮次既不在 group 也不在近期
        bridge(frontier 跳跃时会被吞). 本方法在 open_run 为空(首次/重启后首次)
        时触发, 用 detect_runs_full_coverage(min_run_size=1 + 强制闭合尾部) 把
        gap 轮次全冻结, 保证 [1..cur_round-1] 连续覆盖. 幂等: 跳过已冻结 round_start.
        """
        groups = await conv_service.get_index_groups_up_to(
            self.user_id,
            self.thread_id,
            cur_round - 1,
        )
        db_frontier = groups[-1].round_end if groups else 0
        gap_start = db_frontier + 1
        gap_end = cur_round - 1
        if gap_start > gap_end:
            return

        rounds = await conv_service.conversation_dao.get_conversations_in_range(
            gap_start,
            gap_end,
            self.user_id,
            self.thread_id,
        )
        # 跳过空 summary(无法蒸馏, 同 backfill 约定)
        rounds = [r for r in rounds if (r.summary or "").strip()]
        if not rounds:
            return

        embeddings = self._get_embeddings()
        if embeddings is None:
            return

        entries: list[dict[str, Any]] = []
        for r in rounds:
            try:
                emb = await embeddings.aembed_query(r.summary or "")
            except Exception as e:
                logger.warning(
                    "懒补偿 embedding 失败 round %d, 跳过补偿: %s",
                    r.round_number,
                    e,
                )
                return
            entries.append({
                "round": r.round_number,
                "topic": r.topic or "",
                "summary": r.summary or "",
                "emb": emb,
            })

        runs = detect_runs_full_coverage(entries, threshold=self.similarity_threshold)
        frozen_starts = {g.round_start for g in groups}
        for run in runs:
            if run["start"] in frozen_starts:
                continue
            try:
                await self._freeze_run(conv_service, run["start"], run["end"])
            except Exception as e:
                logger.warning(
                    "懒补偿冻结失败 %d-%d(留 fine bridge): %s",
                    run["start"],
                    run["end"],
                    e,
                )
        logger.info("懒补偿完成: 冻结 gap %d-%d", gap_start, gap_end)

    async def _close_run(
        self,
        conv_service: Any,
        open_run: dict[str, Any],
        next_round: int,
        next_emb: list[float],
    ) -> None:
        """闭合当前 run: 蒸馏弧短语冻结, 开新 run 起于 next_round."""
        key = _run_key(self.user_id, self.thread_id, self.agent_id)
        round_start = open_run["start"]
        round_end = open_run["last"]

        if round_end - round_start + 1 >= _MIN_RUN_SIZE:
            await self._freeze_run(conv_service, round_start, round_end)

        _open_runs[key] = {"start": next_round, "last": next_round, "emb": next_emb}
        logger.info(
            "闭合 run %d-%d(sim<%.2f), 开新 run: round %d",
            round_start,
            round_end,
            self.similarity_threshold,
            next_round,
        )

    async def _freeze_run(
        self,
        conv_service: Any,
        round_start: int,
        round_end: int,
    ) -> None:
        """蒸馏并冻结一个 run 的弧短语."""
        rounds = await conv_service.conversation_dao.get_conversations_in_range(
            round_start,
            round_end,
            self.user_id,
            self.thread_id,
        )
        if not rounds:
            return
        entries = [
            {
                "round": r.round_number,
                "topic": r.topic or "",
                "summary": r.summary or "",
            }
            for r in rounds
        ]
        try:
            arc = await self._get_analyzer().distill(round_start, round_end, entries)
        except Exception as e:
            logger.warning(
                "弧短语蒸馏失败 %d-%d(run 留 fine bridge, 下次不重试): %s",
                round_start,
                round_end,
                e,
            )
            return

        await conv_service.create_index_group(
            user_id=self.user_id,
            thread_id=self.thread_id,
            agent_id=self.agent_id,
            round_start=round_start,
            round_end=round_end,
            arc_phrase=arc,
        )
        logger.info("冻结弧短语 %d-%d: %s", round_start, round_end, arc)
