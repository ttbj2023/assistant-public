"""生成四路对比可读报告 (基于 all.json), 供人工评分与结论."""

from __future__ import annotations

import json
from pathlib import Path

RES = Path(__file__).parent / "results"
ROUTES = ("doubao", "zhipu", "baidu", "gemini")


def main() -> None:
    d = json.loads((RES / "all.json").read_text())
    out: list[str] = ["# 搜索四路对比报告\n"]

    for qid, r in d.items():
        out.append(f"\n## [{qid}] {r['category']} — 预期占优: {r['expect']}")
        out.append(f"**Q:** {r['query']}\n")
        for rt in ROUTES:
            rec = r.get(rt, {})
            if "error" in rec:
                out.append(f"### {rt}  ❌ {rec['error'][:80]}\n")
                continue
            elapsed = rec.get("elapsed", "?")
            results = rec.get("results", [])
            tag = f"⏱{elapsed}s"
            if rec.get("answer"):
                tag += " [总结]"
            out.append(f"### {rt}  ({len(results)}条 {tag})\n")
            for i, it in enumerate(results[:3], 1):
                title = (it.get("title") or "无标题")[:60]
                dom = it.get("domain") or "?"
                site = it.get("site", "")
                site_str = f" [{site}]" if site else ""
                snip = (it.get("snippet") or "").replace("\n", " ")[:140]
                pub = it.get("publish_time", "")[:10]
                byted = " 🔵字节" if it.get("is_bytedance") else ""
                out.append(f"{i}. **{title}** `{dom}`{site_str}{byted} {pub}")
                if snip:
                    out.append(f"   > {snip}")
            if rec.get("answer"):
                ans = rec["answer"].replace("\n", " ")[:280]
                out.append(f"_总结:_ {ans}")
            out.append("")

    out_path = Path(__file__).parent / "comparison.md"
    out_path.write_text("\n".join(out))
    print(out_path)


if __name__ == "__main__":
    main()
