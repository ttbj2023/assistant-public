"""索引弧短语算法实测评估(纯只读, 零写入零 schema 变更).

用真实 conversation_index 数据重放 run 检测 + 弧短语蒸馏, 评估:
- 算法正确性: run 闭合点是否落在可见话题切换处
- 提炼质量: 弧短语叙事性/可区分度/长度
- 旋钮敏感度: 多阈值的 run 粒度对比
- 压缩收益: Σ弧字符 vs Σsummary 字符

用法:
    python scripts/eval_index_arc.py --user bob
    python scripts/eval_index_arc.py --user bob --thresholds 0.4,0.5,0.6
    python scripts/eval_index_arc.py --db /tmp/gifford_ch.db --user gifford
    python scripts/eval_index_arc.py --user bob --no-distill   # 仅检测, 不调 LLM
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("eval_index_arc")


def resolve_db_path(
    user_id: str, thread_id: str, agent_id: str, explicit: str | None
) -> Path:
    if explicit:
        return Path(explicit)
    return (
        PROJECT_ROOT
        / "data"
        / user_id
        / thread_id
        / agent_id
        / "database"
        / "conversation_history.db"
    )


def load_summaries(db_path: Path) -> list[dict]:
    """只读加载 conversation_index 的 round/topic/summary, 按轮序."""
    if not db_path.exists():
        logger.error("数据库不存在: %s", db_path)
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT round_number, topic, summary FROM conversation_index "
        "WHERE summary IS NOT NULL AND summary != '' "
        "ORDER BY round_number",
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


async def embed_summaries(rows: list[dict]) -> list[dict]:
    """对每条 summary 算 embedding, 挂到 entries(批量异步)."""
    from src.inference.embeddings.embeddings import create_embeddings

    embeddings = create_embeddings()
    summaries = [(r["summary"] or "").strip() for r in rows]
    vectors = await embeddings.aembed_documents(summaries)
    entries = []
    for r, emb in zip(rows, vectors, strict=True):
        entries.append({
            "round": r["round_number"],
            "topic": (r["topic"] or "").strip(),
            "summary": (r["summary"] or "").strip(),
            "emb": emb,
        })
    return entries


def print_sweep_metrics(
    entries: list[dict], thresholds: list[float], total_summary_chars: int
) -> dict[float, list[dict]]:
    """三阈值检测对比表, 返回 {threshold: runs}."""
    from src.agent.memory.local_memory.index_run_service import detect_runs

    print("\n" + "=" * 70)
    print("【多阈值 run 粒度对比】")
    print("=" * 70)
    print(f"{'阈值':<8}{'run数':<8}{'平均run长':<12}{'预计弧字符':<14}{'压缩比':<10}")
    print("-" * 70)

    runs_by_thresh: dict[float, list[dict]] = {}
    for t in thresholds:
        runs = detect_runs(entries, threshold=t)
        runs_by_thresh[t] = runs
        run_count = len(runs)
        avg_len = (
            sum(r["end"] - r["start"] + 1 for r in runs) / run_count if run_count else 0
        )
        # 预计弧字符: run数 × max_chars 估算上限
        est_arc_chars = run_count * 40
        ratio = (
            (est_arc_chars / total_summary_chars * 100) if total_summary_chars else 0
        )
        print(f"{t:<8}{run_count:<8}{avg_len:<12.1f}{est_arc_chars:<14}{ratio:<10.1f}%")
    return runs_by_thresh


async def distill_and_print(runs: list[dict], max_chars: int) -> list[tuple[dict, str]]:
    """对每个 run 蒸馏弧短语(模型/参数读 content_analyzer 配置), 返回 [(run, arc), ...]."""
    from src.config.inference_config import get_config as get_inference_config
    from src.inference.content_analyzer.index_arc_analyzer import IndexArcAnalyzer

    ca = get_inference_config().content_analyzer
    analyzer = IndexArcAnalyzer(
        model_id=ca.arc_model or ca.model,
        model_params=ca.arc_model_params,
        max_chars=max_chars,
    )
    results: list[tuple[dict, str]] = []
    usages: list[dict] = []
    for run in runs:
        entries = [
            {"round": e["round"], "topic": e["topic"], "summary": e["summary"]}
            for e in run["entries"]
        ]
        try:
            arc = await analyzer.distill(run["start"], run["end"], entries)
            if analyzer.last_usage:
                usages.append(analyzer.last_usage)
        except Exception as e:
            arc = f"<蒸馏失败: {e}>"
        results.append((run, arc))
        await asyncio.sleep(0.3)
    if usages:
        print_token_stats(usages, max_chars)
    return results


def print_token_stats(usages: list[dict], max_chars: int) -> None:
    """报 token 预算稳定性: output_tokens(含思考) vs max_tokens 上限."""
    from src.config.inference_config import get_config as get_inference_config

    cap = get_inference_config().content_analyzer.arc_model_params.get("max_tokens")
    outputs = [u["output_tokens"] for u in usages if u.get("output_tokens")]
    reasoning = [u["reasoning_tokens"] for u in usages if u.get("reasoning_tokens")]
    inputs = [u["input_tokens"] for u in usages if u.get("input_tokens")]
    if not outputs:
        print("(未取到 token 用量)")
        return
    mx = max(outputs)
    print("\n" + "-" * 70)
    print("【token 预算稳定性】")
    print("-" * 70)
    print(
        f"output_tokens(思考+答案): min={min(outputs)} avg={sum(outputs) // len(outputs)} "
        f"max={mx}  | max_tokens 上限={cap}"
    )
    if cap:
        print(f"  离上限余量: {cap - mx} ({(cap - mx) / cap * 100:.0f}% 未用)")
    if reasoning:
        print(
            f"reasoning_tokens: min={min(reasoning)} avg={sum(reasoning) // len(reasoning)} "
            f"max={max(reasoning)}  (思考占比 max={max(reasoning) / mx * 100:.0f}%)"
        )
    else:
        print("reasoning_tokens: 未上报(minimal 档可能不产生思考 token)")
    if inputs:
        print(f"input_tokens: min={min(inputs)} avg={sum(inputs) // len(inputs)} max={max(inputs)}")


def print_timeline(label: str, arcs: list[tuple[dict, str]]) -> None:
    print("\n" + "=" * 70)
    print(f"【{label} 完整时间线】({len(arcs)} 个弧短语)")
    print("=" * 70)
    for run, arc in arcs:
        span = run["end"] - run["start"] + 1
        rng = f"R{run['start']}" if span == 1 else f"R{run['start']}-{run['end']}"
        print(f"{rng:<14}({span:>3}轮, 闭sim={run['close_sim']:.2f})  {arc}")


def print_detail(arcs: list[tuple[dict, str]]) -> None:
    print("\n" + "=" * 70)
    print("【逐 run 明细】")
    print("=" * 70)
    for run, arc in arcs:
        span = run["end"] - run["start"] + 1
        print(
            f"\n── R{run['start']}-{run['end']} ({span}轮, 闭sim={run['close_sim']:.2f}) ──"
        )
        print(f"弧: {arc}")
        print("源 topics/summaries:")
        for e in run["entries"][:8]:
            print(f"  R{e['round']}: {e['topic']} | {e['summary'][:50]}")
        if len(run["entries"]) > 8:
            print(f"  ... 共 {len(run['entries'])} 轮")


async def run_eval(args: argparse.Namespace) -> None:
    db_path = resolve_db_path(args.user, args.thread, args.agent, args.db)
    rows = load_summaries(db_path)
    if not rows:
        return

    total_summary_chars = sum(len(r["summary"] or "") for r in rows)
    source = args.db or f"{args.user}/{args.thread}/{args.agent}"

    print("=" * 70)
    print(f"索引弧短语实测评估  {source}  ({len(rows)} 轮带 summary)")
    print("=" * 70)

    logger.info("计算 %d 条 summary 的 embedding...", len(rows))
    entries = await embed_summaries(rows)
    thresholds = [float(t) for t in args.thresholds.split(",")]

    runs_by_thresh = print_sweep_metrics(entries, thresholds, total_summary_chars)

    if args.no_distill:
        print("\n[--no-distill] 跳过弧短语蒸馏(仅检测).")
        return

    # 在主阈值蒸馏弧短语(质量深看)
    primary = float(args.distill_threshold)
    if primary not in runs_by_thresh:
        from src.agent.memory.local_memory.index_run_service import detect_runs

        runs_by_thresh[primary] = detect_runs(entries, threshold=primary)

    from src.config.inference_config import get_config as get_inference_config

    inference_config = get_inference_config()
    ca = inference_config.content_analyzer
    arc_model_id = ca.arc_model or ca.model
    logger.info(
        "蒸馏阈值 %.2f 的 %d 个 run (模型 %s)...",
        primary,
        len(runs_by_thresh[primary]),
        arc_model_id,
    )
    arcs = await distill_and_print(runs_by_thresh[primary], args.max_chars)

    print_timeline(f"阈值 {primary}", arcs)
    print_detail(arcs)

    arc_chars = sum(len(a) for _, a in arcs)

    # 全量索引基准: 用真实 formatter 把所有轮渲染成 <index> 表(旧系统同口径)
    from src.storage.formatters.conversation_formatter import (
        create_conversation_formatter,
    )

    formatter = create_conversation_formatter()
    full_items = [
        {
            "round_number": e["round"],
            "topic": e["topic"],
            "summary": e["summary"],
            "created_at": None,
        }
        for e in entries
    ]
    full_index_md = await formatter.format_index_range(full_items, "markdown")
    arc_items = [
        {
            "round_start": run["start"],
            "round_end": run["end"],
            "arc_phrase": arc,
        }
        for run, arc in arcs
    ]
    arc_timeline_md = await formatter.format_index_groups(arc_items)
    full_chars = len(full_index_md)
    timeline_chars = len(arc_timeline_md)

    print("\n" + "=" * 70)
    print("【指标汇总】")
    print("=" * 70)
    print(f"总轮次: {len(rows)} | run 数: {len(arcs)} (阈值 {primary})")
    print(f"Σ弧字符(裸): {arc_chars} vs Σsummary字符(裸): {total_summary_chars}")
    print("-- 全量索引 vs 弧短语时间线(格式化同口径) --")
    print(f"全量索引 <index>: {full_chars} 字符 ({len(rows)} 行)")
    print(f"弧短语 <timeline>: {timeline_chars} 字符 ({len(arcs)} 行)")
    if full_chars > 0:
        ratio = timeline_chars / full_chars * 100
        print(
            f"真实压缩率: {ratio:.1f}% (省 {100 - ratio:.1f}%) "
            f"| 每轮均摊: 全量 {full_chars / len(rows):.0f} → 弧 {timeline_chars / len(rows):.0f} 字符/轮"
        )
    print(f"LLM 调用: {len(arcs)} 次 (distill)")


def main() -> None:
    parser = argparse.ArgumentParser(description="索引弧短语算法实测评估(只读)")
    parser.add_argument("--user", required=True, help="用户ID")
    parser.add_argument("--thread", default="main", help="线程ID (默认 main)")
    parser.add_argument(
        "--agent",
        default="personal-assistant",
        help="Agent ID (默认 personal-assistant)",
    )
    parser.add_argument("--db", default=None, help="显式 DB 路径(生产副本用)")
    parser.add_argument(
        "--thresholds",
        default="0.4,0.5,0.6",
        help="扫描阈值列表(逗号分隔, 默认 0.4,0.5,0.6)",
    )
    parser.add_argument(
        "--distill-threshold",
        default="0.5",
        help="蒸馏弧短语的阈值(默认 0.5)",
    )
    parser.add_argument(
        "--max-chars", type=int, default=40, help="弧短语最大字符(默认 40)"
    )
    parser.add_argument("--no-distill", action="store_true", help="仅检测, 不调 LLM")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    asyncio.run(run_eval(args))


if __name__ == "__main__":
    main()
