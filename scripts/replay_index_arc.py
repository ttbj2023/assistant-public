"""从对话历史回填索引弧短语(backfill 既有对话的 conversation_index_group).

既有对话部署新代码前 conversation_index_group 为空, 直接上新代码会让历史从
索引区消失(frontier 跳过未覆盖轮). 本脚本一次性回填: 按 user/thread/agent
重放 run 检测 + 弧短语蒸馏, 冻结全部既有轮次(强制闭合尾部保证 frontier=N).

只写 conversation_index_group(新建独立表), 永不碰 conversation_index.
幂等: 跳过已冻结的 round_start, 失败仅记日志可重跑.

用法:
    python scripts/replay_index_arc.py --user bob --dry-run     # 预览(蒸馏但不写)
    python scripts/replay_index_arc.py --user bob --apply        # 回填写入
    python scripts/replay_index_arc.py --all --apply             # 全部用户
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
logger = logging.getLogger("replay_index_arc")


def discover_databases(user_filter: str | None) -> list[tuple[str, str, str, Path]]:
    """扫描 data/*/*/*/database/conversation_history.db, 返回 [(user, thread, agent, path)]."""
    results: list[tuple[str, str, str, Path]] = []
    for db_path in sorted(
        PROJECT_ROOT.glob("data/*/*/*/database/conversation_history.db")
    ):
        parts = db_path.parts
        # data/<user>/<thread>/<agent>/database/conversation_history.db
        try:
            idx = parts.index("data")
            user, thread, agent = parts[idx + 1], parts[idx + 2], parts[idx + 3]
        except (ValueError, IndexError):
            continue
        if user_filter and user != user_filter:
            continue
        results.append((user, thread, agent, db_path))
    return results


def load_summaries(db_path: Path) -> list[dict]:
    """只读加载 conversation_index 的 round/topic/summary, 按轮序."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT round_number, topic, summary FROM conversation_index "
        "WHERE summary IS NOT NULL AND summary != '' ORDER BY round_number",
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


async def embed_summaries(rows: list[dict]) -> list[dict]:
    """对每条 summary 算 embedding(批量异步)."""
    from src.inference.embeddings.embeddings import create_embeddings

    embeddings = create_embeddings()
    summaries = [(r["summary"] or "").strip() for r in rows]
    vectors = await embeddings.aembed_documents(summaries)
    return [
        {
            "round": r["round_number"],
            "topic": (r["topic"] or "").strip(),
            "summary": (r["summary"] or "").strip(),
            "emb": emb,
        }
        for r, emb in zip(rows, vectors, strict=True)
    ]


async def backfill_one(
    user: str,
    thread: str,
    agent: str,
    threshold: float,
    max_chars: int,
    apply: bool,
) -> dict:
    """回填单个对话库, 返回统计."""
    # detect_runs_full_coverage 已迁回 index_run_service(在线懒补偿共用)
    from src.agent.memory.local_memory.index_run_service import (
        detect_runs_full_coverage,
    )
    from src.config.inference_config import get_config as get_inference_config
    from src.inference.content_analyzer.index_arc_analyzer import IndexArcAnalyzer
    from src.storage.service import create_conversation_service

    conv_service = await create_conversation_service(user, thread, agent_id=agent)
    db_path = (
        PROJECT_ROOT
        / "data"
        / user
        / thread
        / agent
        / "database"
        / "conversation_history.db"
    )
    rows = load_summaries(db_path)
    if not rows:
        logger.warning("[%s/%s/%s] 无 summary, 跳过", user, thread, agent)
        return {"rounds": 0, "runs": 0, "frozen": 0, "skipped": 0}

    n_rounds = rows[-1]["round_number"]
    logger.info(
        "[%s/%s/%s] %d 轮, 模式=%s",
        user,
        thread,
        agent,
        n_rounds,
        "APPLY(写入)" if apply else "DRY-RUN(预览)",
    )

    # 幂等: 已冻结的 round_start 跳过
    existing = await conv_service.get_index_groups_up_to(user, thread, n_rounds)
    frozen_starts = {g.round_start for g in existing}

    entries = await embed_summaries(rows)
    runs = detect_runs_full_coverage(entries, threshold=threshold)

    ca = get_inference_config().content_analyzer
    analyzer = IndexArcAnalyzer(
        model_id=ca.arc_model or ca.model,
        model_params=ca.arc_model_params,
        max_chars=max_chars,
    )

    frozen = 0
    skipped = 0
    for run in runs:
        if run["start"] in frozen_starts:
            skipped += 1
            continue
        entries_plain = [
            {"round": e["round"], "topic": e["topic"], "summary": e["summary"]}
            for e in run["entries"]
        ]
        try:
            arc = await analyzer.distill(run["start"], run["end"], entries_plain)
        except Exception as e:
            logger.error(
                "[%s/%s/%s] R%d-%d 蒸馏失败(跳过, 可重跑): %s",
                user,
                thread,
                agent,
                run["start"],
                run["end"],
                e,
            )
            continue
        span = run["end"] - run["start"] + 1
        logger.info(
            "  R%d-%d (%d轮): %s",
            run["start"],
            run["end"],
            span,
            arc,
        )
        if apply:
            await conv_service.create_index_group(
                user_id=user,
                thread_id=thread,
                agent_id=agent,
                round_start=run["start"],
                round_end=run["end"],
                arc_phrase=arc,
            )
        frozen += 1
        await asyncio.sleep(0.3)

    logger.info(
        "[%s/%s/%s] 完成: %d runs, 冻结 %d, 跳过(已存在) %d",
        user,
        thread,
        agent,
        len(runs),
        frozen,
        skipped,
    )
    return {"rounds": n_rounds, "runs": len(runs), "frozen": frozen, "skipped": skipped}


async def verify_one(user: str, thread: str, agent: str) -> bool:
    """验证 backfill 覆盖完整性: [1..N] 无 gap."""
    from src.storage.service import create_conversation_service

    db_path = (
        PROJECT_ROOT
        / "data"
        / user
        / thread
        / agent
        / "database"
        / "conversation_history.db"
    )
    rows = load_summaries(db_path)
    if not rows:
        return True
    # 期望覆盖 = 有 summary 的轮(无 summary 的轮本就无法蒸馏, 不计入期望)
    expected = {r["round_number"] for r in rows}
    n_rounds = max(expected) if expected else 0
    conv_service = await create_conversation_service(user, thread, agent_id=agent)
    groups = await conv_service.get_index_groups_up_to(user, thread, n_rounds)
    covered: set[int] = set()
    for g in groups:
        covered.update(range(g.round_start, g.round_end + 1))
    missing = expected - covered
    extra = len([g for g in groups if not (g.arc_phrase or "").strip()])
    ok = not missing and extra == 0
    status = "OK" if ok else "FAIL"
    logger.info(
        "[%s/%s/%s] 验证 %s: %d groups 覆盖 %d/%d 轮, 空弧=%d",
        user,
        thread,
        agent,
        status,
        len(groups),
        len(covered),
        n_rounds,
        extra,
    )
    if missing:
        logger.warning(
            "  缺失轮次(前20): %s",
            sorted(missing)[:20],
        )
    return ok


async def run(args: argparse.Namespace) -> None:
    dbs = discover_databases(args.user)
    if not dbs:
        logger.error("未找到匹配的 conversation_history.db")
        return
    logger.info("发现 %d 个对话库", len(dbs))

    all_ok = True
    total = {"rounds": 0, "runs": 0, "frozen": 0, "skipped": 0}
    for user, thread, agent, _ in dbs:
        try:
            stats = await backfill_one(
                user,
                thread,
                agent,
                threshold=args.threshold,
                max_chars=args.max_chars,
                apply=args.apply,
            )
            for k in total:
                total[k] += stats[k]
            if args.apply and args.verify:
                ok = await verify_one(user, thread, agent)
                all_ok = all_ok and ok
        except Exception as e:
            logger.exception("[%s/%s/%s] 处理失败: %s", user, thread, agent, e)
            all_ok = False

    logger.info("=" * 60)
    logger.info(
        "汇总: %d 库, %d 轮, %d runs, 冻结 %d, 跳过 %d",
        len(dbs),
        total["rounds"],
        total["runs"],
        total["frozen"],
        total["skipped"],
    )
    if not args.apply:
        logger.info("(DRY-RUN 未写入; 加 --apply 回填)")


def main() -> None:
    parser = argparse.ArgumentParser(description="回填既有对话的索引弧短语")
    parser.add_argument("--user", default=None, help="指定用户(默认全部)")
    parser.add_argument(
        "--apply", action="store_true", help="实际写入(默认 dry-run 预览)"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="run 检测相似度阈值(默认 0.5)",
    )
    parser.add_argument(
        "--max-chars", type=int, default=60, help="弧短语最大字符(默认 60)"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="apply 后验证覆盖完整性(默认 apply 时启用)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
