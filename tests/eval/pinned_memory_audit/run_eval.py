"""置顶记忆审计评估脚本.

对审计机制在评测 fixture 上的表现做量化评估.

三类 GT 判断(对应三种 operation):
- delete: 明确不合格 → 直接删
- change: 灰色地带(含偏好内核但表述状态化) → 提炼改写
- merge:  语义重复 → 合并

指标(精确率优先):
- recall:    该处理的(delete+change.old+merge原始行)被审计消除的比例. 负样本无应处理项 → N/A
- precision: 审计消除的行里, 属于"应处理"的比例. 负样本强制(删任何行=误删)
- merge_residual: 每个 merge_group 原始行剩余数(理想 ≤1)
- false_removed: 误删行数(删了该保留的好记忆, 最严重)

实验维度:
- with_history: 是否提供对话索引给审计(对照: 纯置顶判断 vs 有历史辅助)

用法:
    python run_eval.py                          # mock 理想审计验证框架
    python run_eval.py --no-history             # 无历史对照(mock 亦满分, 真实才见差异)
    python run_eval.py --real --window 20       # 阶段2接入真实审计
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import pathlib
import sys
from dataclasses import dataclass, field

_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

FIX_DIR = pathlib.Path(__file__).parent / "fixtures"
_FIELDS = ("basic_info", "preferences")


@dataclass
class AuditConfig:
    model: str = "mock"
    window: int = 20
    prompt_version: str = "v0"
    with_history: bool = True


@dataclass
class AuditOutput:
    operations: list[dict] = field(default_factory=list)
    tokens: int = 0
    judgments: dict = field(default_factory=dict)


@dataclass
class Metrics:
    sample_id: str
    sample_type: str
    recall: float
    precision: float
    merge_residual: list[int]
    false_removed: int
    audit_removed: int
    gt_process_count: int


def _split_lines(content: str) -> list[str]:
    return [ln.strip() for ln in (content or "").split("\n") if ln.strip()]


def apply_operations(pinned: dict, operations: list[dict]) -> dict[str, list[str]]:
    """应用 operations 到 snapshot, 返回审计后各 field 的行列表."""
    result: dict[str, list[str]] = {}
    for fld in _FIELDS:
        result[fld] = _split_lines(pinned.get(fld, ""))

    for op in operations:
        action = op.get("action")
        fld = op.get("field", "")
        if fld not in result:
            continue
        content = (op.get("content") or "").strip()
        old = (op.get("old_content") or "").strip()
        new = (op.get("new_content") or "").strip()

        if action == "delete" and content and content in result[fld]:
            result[fld].remove(content)
        elif action == "change" and old and new:
            try:
                idx = result[fld].index(old)
                result[fld][idx] = new
            except ValueError:
                pass
    return result


def compute_metrics(
    sample_id: str,
    sample_type: str,
    pinned: dict,
    operations: list[dict],
    gt: dict,
    tokens: int,
) -> Metrics:
    """计算指标. 该处理的 = delete + change.old + merge原始行."""
    original_lines: set[str] = set()
    for fld in _FIELDS:
        original_lines.update(_split_lines(pinned.get(fld, "")))

    after = apply_operations(pinned, operations)
    after_lines: set[str] = set()
    for lines in after.values():
        after_lines.update(lines)

    audit_removed = original_lines - after_lines

    # 确定该消失的(delete + change.old): 用于 recall
    gt_must_remove: set[str] = set(gt.get("delete", []))
    for ch in gt.get("change", []):
        old = ch.get("old_content", "")
        if old:
            gt_must_remove.add(old)

    # 可接受消除(再含 merge 原始行): 用于 precision(merge 行消失不算误删)
    gt_acceptable = set(gt_must_remove)
    for group in gt.get("merge_groups", []):
        gt_acceptable.update(group)

    if gt_must_remove:
        recall = len(audit_removed & gt_must_remove) / len(gt_must_remove)
    else:
        recall = float("nan")

    if audit_removed:
        precision = len(audit_removed & gt_acceptable) / len(audit_removed)
    else:
        precision = 1.0

    false_removed = len(audit_removed - gt_acceptable)

    merge_residual: list[int] = []
    for group in gt.get("merge_groups", []):
        merge_residual.append(sum(1 for ln in group if ln in after_lines))

    return Metrics(
        sample_id=sample_id,
        sample_type=sample_type,
        recall=recall,
        precision=precision,
        merge_residual=merge_residual,
        false_removed=false_removed,
        audit_removed=len(audit_removed),
        gt_process_count=len(gt_must_remove),
    )


async def mock_ideal_audit(fixture: dict, config: AuditConfig) -> AuditOutput:
    """基于 GT 生成理想 operations(验证框架用).

    delete 所有 GT delete; change 所有 GT change(提炼占位); merge 保留首条删其余.
    预期: recall=100%, precision=100%, merge_residual=[1], false_removed=0.
    with_history 不影响 mock(基于GT, 与历史无关).
    """
    pinned = fixture["pinned_memory"]
    gt = fixture["ground_truth"]
    ops: list[dict] = []

    for fld in _FIELDS:
        lines = _split_lines(pinned.get(fld, ""))
        for d in gt.get("delete", []):
            if d in lines:
                ops.append({"action": "delete", "field": fld, "content": d})
        for ch in gt.get("change", []):
            old = ch.get("old_content", "")
            if old in lines:
                ops.append({
                    "action": "change",
                    "field": fld,
                    "old_content": old,
                    "new_content": old[:12] + "(提炼)",
                })
        for group in gt.get("merge_groups", []):
            in_field = [g for g in group if g in lines]
            for g in in_field[1:]:
                ops.append({"action": "delete", "field": fld, "content": g})

    return AuditOutput(operations=ops, tokens=0)


async def run_single(
    fixture: dict, audit_fn, config: AuditConfig
) -> tuple[Metrics, int, list[dict]]:
    output = await audit_fn(fixture, config)
    m = compute_metrics(
        fixture["sample_id"],
        fixture["sample_type"],
        fixture["pinned_memory"],
        output.operations,
        fixture["ground_truth"],
        output.tokens,
    )
    return m, output.tokens, output.operations


def _fmt_pct(x: float) -> str:
    if math.isnan(x):
        return "  N/A"
    return f"{x:.0%}"


def format_row(m: Metrics, tokens: int) -> str:
    merge = ",".join(str(x) for x in m.merge_residual) if m.merge_residual else "-"
    return (
        f"{m.sample_id:24s} {_fmt_pct(m.recall):>6s}  "
        f"{_fmt_pct(m.precision):>6s}  [{merge:>5s}]  "
        f"false={m.false_removed}  tok={tokens}"
    )


def load_fixtures() -> list[dict]:
    return [
        json.loads((FIX_DIR / f).read_text(encoding="utf-8"))
        for f in sorted(FIX_DIR.glob("*.json"))
        if not f.name.startswith("_")
    ]


async def main() -> None:
    parser = argparse.ArgumentParser(description="置顶记忆审计评估")
    parser.add_argument("--real", action="store_true", help="接入真实审计(阶段2)")
    parser.add_argument("--model", default="deepseek:deepseek-v4-flash")
    parser.add_argument("--window", type=int, default=20)
    parser.add_argument(
        "--no-history", action="store_true", help="不提供对话历史(纯置顶判断对照)"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="打印每个样本的operations详情"
    )
    args = parser.parse_args()

    fixtures = load_fixtures()
    config = AuditConfig(
        model=args.model,
        window=args.window,
        with_history=not args.no_history,
    )

    if args.real:
        try:
            from real_audit import real_audit  # type: ignore[import-not-found]
        except ImportError:
            print("real_audit 未实现(阶段2), 回退 mock")
            audit_fn = mock_ideal_audit
        else:
            audit_fn = real_audit
    else:
        audit_fn = mock_ideal_audit

    mode = "真实审计" if args.real else "Mock 理想审计(验证框架, 应满分)"
    hist = (
        f"历史={f'有(window={args.window})' if config.with_history else '无(纯置顶)'}"
    )
    print(f"=== {mode} | model={args.model} | {hist} ===\n")

    header = f"{'sample':24s} {'recall':>6s}  {'prec':>6s}  merge    metrics"
    print(header)
    print("-" * 70)
    for fx in fixtures:
        m, tokens, ops = await run_single(fx, audit_fn, config)
        print(format_row(m, tokens))
        if args.verbose:
            for op in ops:
                if op["action"] == "delete":
                    print(f"      DEL {op['content'][:60]}")
                else:
                    print(
                        f"      CHG {op['old_content'][:30]} -> {op['new_content'][:40]}"
                    )


if __name__ == "__main__":
    asyncio.run(main())
