"""4 模型分歧分析 - 对分歧样本输出逐条判断矩阵 + 分歧理由.

每条置顶显示 GT 与 4 模型判断(K/D/C), 分歧行额外打印各模型的判断理由,
供人工评判模型判断逻辑是否合理.
"""

from __future__ import annotations

import asyncio
import pathlib

from real_audit import format_memory_with_numbers, real_audit
from run_eval import AuditConfig, load_fixtures

FIX_DIR = pathlib.Path(__file__).parent / "fixtures"

MODELS = [
    ("flash", "deepseek:deepseek-v4-flash"),
    ("ds-pro", "deepseek:deepseek-v4-pro"),
    ("doubao", "doubao:doubao-seed-2-0-pro-260215"),
    ("gpt55", "openai:gpt-5.5"),
]

DIVERGENT = [
    "syn_medical",
    "syn_verbose",
    "persona_translator",
    "syn_demands",
    "syn_mixed",
    "syn_financial",
    "gifford_dirty",
]

_ACT = {"keep": "K", "delete": "D", "change": "C"}


def _gt_map(number_map, gt):
    delete_set = set(gt.get("delete", []))
    change_set = {c["old_content"] for c in gt.get("change", [])}
    result = {}
    for n, info in number_map.items():
        c = info["content"]
        if c in delete_set:
            result[n] = "D"
        elif c in change_set:
            result[n] = "C"
        else:
            result[n] = "K"
    return result


async def main():
    fixtures = {fx["sample_id"]: fx for fx in load_fixtures()}
    legend = "GT | " + "  ".join(f"{m[0]:5s}" for m, _ in MODELS)
    print("图例:", legend, "(K=keep D=del C=chg)")
    print("=" * 95)

    for sid in DIVERGENT:
        if sid not in fixtures:
            continue
        fx = fixtures[sid]
        block, number_map = format_memory_with_numbers(fx["pinned_memory"])
        gt = _gt_map(number_map, fx["ground_truth"])

        model_jm: dict[str, dict] = {}
        for name, mid in MODELS:
            config = AuditConfig(model=mid, with_history=True, window=20)
            try:
                output = await real_audit(fx, config)
                model_jm[name] = output.judgments
            except Exception as e:
                print(f"  [{name}] 失败: {e}")
                model_jm[name] = {}

        print(f"\n=== {sid} ({fx['description'][:34]}) ===")
        divergent_nums: list[int] = []
        for n in sorted(number_map):
            content = number_map[n]["content"][:36]
            cells = [
                _ACT.get(model_jm[m].get(n, {}).get("action", "keep"), "K")
                for m, _ in MODELS
            ]
            line = f"[{n:2d}] {content:38s} {gt[n]}  " + "   ".join(
                f"{c}" for c in cells
            )
            if len(set(cells + [gt[n]])) > 1:
                line += "  ←分歧"
                divergent_nums.append(n)
            print(line)

        if divergent_nums:
            print("  ── 分歧理由 ──")
            for n in divergent_nums:
                print(f"  [{n}] {number_map[n]['content'][:54]}")
                for m, _ in MODELS:
                    j = model_jm[m].get(n, {})
                    reason = j.get("reason", "")[:70]
                    print(
                        f"      {m:8s}: {_ACT.get(j.get('action', '?'), '?')} — {reason}"
                    )


if __name__ == "__main__":
    asyncio.run(main())
