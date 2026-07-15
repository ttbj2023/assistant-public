"""Markdown 测试报告生成."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.conversation_test_lib.analyzers import (
    _analyze_history_messages,
    _check_attachment_markers,
    _count_index_rounds,
    _count_rounds,
    _count_todo_items,
    _enrich_rounds,
    _evaluate_user_requirement,
    _extract_section,
    _extract_tokens,
    _is_scnet_model,
    _pinned_evolution,
)
from scripts.conversation_test_lib.collectors import collect_usage_stats
from scripts.conversation_test_lib.config import ConversationTestConfig
from scripts.conversation_test_lib.formatting import _truncate


def generate_report(
    conv_results: list[dict[str, Any]],
    db_data: dict[str, Any],
    tool_logs: list[dict[str, Any]],
    prompt_logs: list[dict[str, Any]],
    server_errors: list[dict[str, Any]],
    config: ConversationTestConfig,
    session_start: float,
) -> Path:
    """生成 Markdown 报告."""
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    use_all = len(config.conversations) > len(conv_results) or bool(
        # full 模式轮数显著多于 quick, 用数据本身判断更稳
        [c for c in config.conversations if c.get("tag") == "召回-R1身份"]
    )
    mode = "full" if use_all else "quick"
    report_path = (
        config.logs_dir / f"conversation_test_report_{ts}_{mode}_{config.agent_id}.md"
    )
    config.logs_dir.mkdir(parents=True, exist_ok=True)

    mode_label = "60轮完整版" if use_all else "24轮精简版"

    models_used = sorted({
        str(p.get("metadata", {}).get("model", "")).strip()
        for p in prompt_logs
        if p.get("metadata", {}).get("model")
    })
    model_display = (
        ", ".join(models_used) if models_used else f"(未知, agent={config.agent_id})"
    )

    lines: list[str] = []
    w = lines.append

    w(f"# 对话测试报告 ({mode_label})")
    w("")
    w(f"- **时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    w(f"- **用户**: {config.user_id} / 线程: {config.thread_id}")
    w(f"- **Agent**: {config.agent_id}")
    w(f"- **模型**: {model_display}")
    w(f"- **轮数**: {len(conv_results)}")
    w("")

    enrich = _enrich_rounds(conv_results, tool_logs, db_data.get("conversations"))

    # ---- 1. 执行摘要 ----
    w("## 1. 对话执行摘要")
    w("")
    w("| 轮次 | 标签 | 状态 | 总s | LLM调用 | LLM_s | 工具 | 入tok | 出tok | 摘要 |")
    w("|------|------|------|-----|---------|-------|------|-------|-------|------|")
    for r in conv_results:
        rnd = r["round"]
        status = r["status_code"]
        status_str = (
            "✅" if (isinstance(status, int) and status == 200) else f"❌ {status}"
        )
        e = enrich.get(int(rnd), {})
        llm_calls = e.get("llm_calls", 0)
        llm_ms = e.get("llm_ms", 0)
        llm_s = f"{llm_ms / 1000:.1f}" if llm_ms else "0"
        tool_col = _truncate(e.get("tool_sequence", "") or "—", 50).replace("|", "\\|")
        in_tok, out_tok = _extract_tokens(r.get("response"))
        resp = r.get("response")
        if isinstance(resp, dict):
            msg = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        else:
            msg = str(resp or "")
        summary = _truncate(msg, 45).replace("|", "\\|").replace("\n", " ")
        w(
            f"| R{rnd:02d} | {r['tag']} | {status_str} | {r['elapsed']} | "
            f"{llm_calls} | {llm_s} | {tool_col} | {in_tok} | {out_tok} | {summary} |"
        )
    w("")

    # ---- 用量统计 ----
    w("## 用量统计 (精确 · 来自 usage.db)")
    w("")
    usage_stats = collect_usage_stats(session_start, config.user_id)
    if not usage_stats.get("available"):
        w(f"⚠️ {usage_stats.get('reason', '不可用')}")
        w("")
    else:
        u_rows = usage_stats.get("rows", [])
        if not u_rows:
            w("本 session 时间窗内无用量记录.")
            w("")
        else:
            lo_w, hi_w = usage_stats.get("window", ("?", "?"))
            w(f"时间窗: {lo_w} ~ {hi_w} (UTC, 含 reasoning_tokens)")
            w("")

            main_rows = [r for r in u_rows if r.get("usage_source") == "main_chat"]
            w("### 主对话模型 (`usage_source=main_chat`)")
            w("")
            if main_rows:
                w("| 模型 | 调用 | 入tok | 出tok | 推理tok | 总tok |")
                w("|------|------|-------|-------|---------|-------|")
                m_calls = m_in = m_out = m_reason = m_total = 0
                for r in main_rows:
                    c = r.get("calls", 0) or 0
                    ci = r.get("in_tok", 0) or 0
                    co = r.get("out_tok", 0) or 0
                    cr = r.get("reason_tok", 0) or 0
                    ct = r.get("total_tok", 0) or 0
                    m_calls += c
                    m_in += ci
                    m_out += co
                    m_reason += cr
                    m_total += ct
                    w(f"| {r.get('model_id', '?')} | {c} | {ci} | {co} | {cr} | {ct} |")
                w(
                    f"| **小计** | **{m_calls}** | **{m_in}** | **{m_out}** | "
                    f"**{m_reason}** | **{m_total}** |"
                )
            else:
                w("无 main_chat 记录 (主对话模型可能未走 scnet).")
            w("")

            sub: dict[str, list[dict[str, Any]]] = {}
            for r in u_rows:
                src = r.get("usage_source", "?")
                if src != "main_chat":
                    sub.setdefault(src, []).append(r)
            if sub:
                w("### 子系统用量 (按来源)")
                w("")
                w("| 来源 | 调用 | 总tok |")
                w("|------|------|-------|")
                for src in sorted(sub):
                    rs = sub[src]
                    c = sum(x.get("calls", 0) or 0 for x in rs)
                    t = sum(x.get("total_tok", 0) or 0 for x in rs)
                    w(f"| {src} | {c} | {t} |")
                w("")

            scnet_rows = [r for r in u_rows if _is_scnet_model(r.get("model_id"))]
            w("### scnet 用量小计 (对应供应商 Credits)")
            w("")
            if scnet_rows:
                si = sum(r.get("in_tok", 0) or 0 for r in scnet_rows)
                so = sum(r.get("out_tok", 0) or 0 for r in scnet_rows)
                sr = sum(r.get("reason_tok", 0) or 0 for r in scnet_rows)
                st = sum(r.get("total_tok", 0) or 0 for r in scnet_rows)
                w(f"- **入**: {si} tok")
                w(f"- **出**(含推理 {sr} tok): {so} tok")
                w(f"- **总**: {st} tok")
                est_credits = si * 0.01 + so * 0.05
                w(
                    f"- **估算 Credits**: ~{est_credits:.2f} "
                    "(按 入0.01/出0.05 token 单点拟合, 待对照后台增量校准)"
                )
                w("")
                w(
                    "> 标定: 对照 scnet 后台 Credits 增量 Δ. 若 Δ ≈ 估算值, "
                    "单价假设成立; 否则用 Δ 与 token 量解真实单价."
                )
                w("")
            else:
                w("无 scnet 用量记录 (主模型未走 scnet, 或本 session 无 LLM 调用).")
                w("")

    # ---- 2. 慢轮次与异常详情 ----
    w("## 2. 慢轮次与异常详情")
    w("")
    SLOW_THRESHOLD = 300
    att_result = _check_attachment_markers(
        conv_results, db_data.get("attachment_ids") or set()
    )
    fake_by_label: dict[str, list[str]] = {
        item["label"]: item["fake_ids"] for item in att_result["unregistered"]
    }
    slow_rounds: list[dict[str, Any]] = []
    anomaly_rounds: list[dict[str, Any]] = []
    for r in conv_results:
        real_reasons: list[str] = []
        if isinstance(r.get("status_code"), int) and r["status_code"] != 200:
            real_reasons.append(f"HTTP {r['status_code']}")
        label = f"R{r['round']:02d}({r.get('tag', '')})"
        if label in fake_by_label:
            fakes = fake_by_label[label]
            real_reasons.append(
                f"{len(fakes)} 个未注册附件标记(疑似幻觉): {', '.join(fakes)}"
            )
        if real_reasons:
            anomaly_rounds.append({"round": r, "reasons": real_reasons})
        try:
            elapsed = float(r.get("elapsed", 0))
        except (ValueError, TypeError):
            elapsed = 0
        if elapsed > SLOW_THRESHOLD:
            slow_rounds.append({"round": r, "elapsed": elapsed})

    has_content = False
    if slow_rounds:
        slow_rounds.sort(key=lambda x: x["elapsed"], reverse=True)
        w(f"### 慢轮次 (>{SLOW_THRESHOLD}s, 共 {len(slow_rounds)} 轮)")
        w("")
        for item in slow_rounds:
            r = item["round"]
            w(f"**R{r['round']:02d} — {r['tag']}** (耗时 {item['elapsed']:.0f}s)")
            w("")
            w(f"用户: {r['user_input']}")
            w("")
        has_content = True

    if anomaly_rounds:
        w(f"### 异常轮次 (共 {len(anomaly_rounds)} 轮)")
        w("")
        for item in anomaly_rounds:
            r = item["round"]
            w(f"**R{r['round']:02d} — {r['tag']}** ({', '.join(item['reasons'])})")
            w("")
            w(f"**用户输入**: {r['user_input']}")
            w("")
            resp = r.get("response")
            if isinstance(resp, dict):
                msg = (
                    resp
                    .get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "(空)")
                )
                w("**助手回复**:")
                w("")
                w(f"{msg[:500]}{'...' if len(msg) > 500 else ''}")
            else:
                w("**原始响应**:")
                w("")
                w("```")
                w(str(resp)[:500])
                w("```")
            w("")
        has_content = True

    if not has_content:
        w("✅ 所有轮次响应正常，无慢轮次或异常")
        w("")

    # ---- 3. 数据库内容 ----
    w("## 3. 数据库内容")
    w("")

    w("### 3.1 conversation_history.db")
    w("")
    convs = db_data.get("conversations", [])
    w(f"共 {len(convs)} 条记录")
    w("")
    if convs:
        for c in [convs[0], convs[-1]]:
            rn = c.get("round_number", "?")
            user_msg = str(c.get("user_message", ""))[:100].replace("\n", " ")
            asst_msg = str(c.get("assistant_response", ""))[:100].replace("\n", " ")
            w(f"- **Round {rn}** | 用户: `{user_msg}` | 助手: `{asst_msg}`")
        if len(convs) > 2:
            w(f"- ... (省略 {len(convs) - 2} 条)")
        w("")

    index_groups = [g for g in db_data.get("index_groups", []) if "error" not in g]
    w("#### 3.1.1 索引弧短语分组 (conversation_index_group)")
    w("")
    w(f"共 {len(index_groups)} 条冻结分组")
    w("")
    if index_groups:
        w("| 轮次范围 | 弧短语 |")
        w("|---------|---------|")
        for g in index_groups:
            rs = g.get("round_start", "?")
            re_ = g.get("round_end", "?")
            rng = f"{rs}-{re_}" if rs != re_ else f"{rs}"
            arc = str(g.get("arc_phrase", "")).replace("|", "\\|").replace("\n", " ")
            w(f"| {rng} | {arc} |")
        w("")
        w(
            "> 老期对话经语义 run 检测闭合后, 由 LLM 蒸馏为弧短语冻结至此表; "
            "近期未冻结的全索引走 prompt 内 <index> bridge 区, 不在此表. "
            "弧短语质量/是否合理冻结交人工判断.",
        )
        w("")
    else:
        w("*(无冻结分组 - 对话可能未触发 run 闭合, 或为旧数据无此表)*")
        w("")

    w("### 3.2 pinned_memory.db")
    w("")
    pinned = db_data.get("pinned_memory", [])
    w(f"共 {len(pinned)} 条记录")
    w("")
    for p in pinned:
        w(f"**更新时间**: {p.get('updated_at', '?')}")
        content = str(p.get("content", p.get("pinned_content", "")))
        w("```")
        w(content[:3000])
        w("```")
        w("")

    if pinned:
        dedup_note = (
            "> **置顶记忆语义去重**: 配置 `dedup_enabled` 时, 重复/相似表述的 add "
            "应按嵌入向量余弦相似度去重. "
        )
        if use_all:
            dedup_note += "full 版含「置顶记忆去重验证」轮次, 若上表出现内容高度重叠的多条记录, 可能表示去重未生效."
        else:
            dedup_note += "若上表出现内容高度重叠的多条记录, 可结合 full 版去重验证轮次进一步排查."
        w(dedup_note)
        w("")

    w("### 3.3 todo.db")
    w("")
    todos = db_data.get("todos", [])
    w(f"共 {len(todos)} 条记录")
    w("")
    if todos:
        w("| ID | 标题 | 状态 | 优先级 | 创建时间 |")
        w("|----|------|------|--------|---------|")
        for t in todos:
            tid = str(t.get("id", "?"))[:12]
            title = str(t.get("title", ""))[:30].replace("|", "\\|")
            status_t = t.get("status", "?")
            prio = t.get("priority", "?")
            created = str(t.get("created_at", "?"))[:19]
            w(f"| {tid} | {title} | {status_t} | {prio} | {created} |")
        w("")
    else:
        w("*(无记录)*")
        w("")

    w("### 3.4 file_registry.db (用户级, 附件注册表)")
    w("")
    attachments = [a for a in db_data.get("attachments", []) if "error" not in a]
    w(
        f"共 {len(attachments)} 条记录 (用户级 SSOT, 供 Ch2/Ch8 [file: file_id] 标记反幻觉核验)"
    )
    w("")
    if attachments:
        w("| file_id | 类型 | 文件名 | 轮次 | 概要 |")
        w("|---------|------|--------|------|------|")
        for a in attachments:
            fid = a.get("file_id", "?")
            ftype = a.get("file_type", "?")
            fname = str(a.get("filename", ""))[:30].replace("|", "\\|")
            rn = a.get("round_number", "?")
            rn_str = f"R{rn:02d}" if isinstance(rn, int) else str(rn)
            brief = str(a.get("brief", ""))[:40].replace("|", "\\|").replace("\n", " ")
            w(f"| {fid} | {ftype} | {fname} | {rn_str} | {brief} |")
        w("")
    else:
        w("*(无附件记录)*")
        w("")

    w("### 3.5 置顶记忆演进")
    w("")
    pinned_evo = _pinned_evolution(conv_results, prompt_logs)
    if pinned_evo:
        w(
            "> 每轮 prompt 的 system_prompt 含 <pinned_memory> 块, 逐轮对比反映记忆的"
            "实际变化 (经过去重/精筛后, 即模型实际所见). prompt N↔N+1 的变化由 round N "
            "的对话触发. 内容质量 (是否该记/是否准确) 交由人工判断."
        )
        w("")
        first_present = next((e for e in pinned_evo if e["present"]), None)
        first_round = first_present["round"] if first_present else None
        changed = [e for e in pinned_evo if e["added"] or e["removed"]]
        if not changed and first_round is None:
            w("*(全程无置顶记忆)*")
            w("")
        else:
            w("| 轮次 | +新增 | -移除 |")
            w("|------|-------|-------|")
            for e in pinned_evo:
                if e["round"] == first_round or e["added"] or e["removed"]:
                    w(
                        f"| R{e['round']:02d} | +{len(e['added'])} | -{len(e['removed'])} |"
                    )
            w("")
            if first_present:
                w(f"**首次出现 (R{first_present['round']:02d}):**")
                w("```")
                w(first_present["block"][:1500])
                w("```")
                w("")
            for e in changed:
                w(f"**R{e['round']:02d} 变化:**")
                for ln in e["added"]:
                    w(f"- ➕ {ln[:120]}")
                for ln in e["removed"]:
                    w(f"- ➖ {ln[:120]}")
                w("")
    else:
        w("*(无 prompt 捕获, 无法计算演进)*")
        w("")

    # ---- 4. 向量数据库 ----
    w("## 4. 向量数据库")
    w("")
    vec = db_data.get("vector", {})
    if vec.get("status") == "not_found":
        w("*(未找到 chroma.sqlite3)*")
    else:
        w(f"- **文件大小**: {vec.get('chroma_size_human', '?')}")
        w(f"- **Collections**: {vec.get('collections_count', '?')}")
        w(f"- **Embeddings**: {vec.get('embeddings_count', '?')}")
        if vec.get("error"):
            w(f"- **错误**: {vec['error']}")
    w("")

    # ---- 5. 工具与 LLM 调用日志 ----
    w("## 5. 工具与 LLM 调用日志")
    w("")
    if tool_logs:
        tool_summary: dict[str, list[dict[str, Any]]] = {}
        for ev in tool_logs:
            etype = ev.get("type", "unknown")
            data_inner = ev.get("data", {})
            if etype in ("tool_start", "tool_end", "tool_error"):
                tool_name = data_inner.get("tool_name", "unknown")
                if tool_name not in tool_summary:
                    tool_summary[tool_name] = []
                tool_summary[tool_name].append({"type": etype, **data_inner})

        w("### 工具调用统计")
        w("")
        w("| 工具 | 调用次数 | 成功 | 错误 | 平均耗时 |")
        w("|------|---------|------|------|---------|")
        for tname, events in tool_summary.items():
            starts = [e for e in events if e["type"] == "tool_start"]
            ends = [e for e in events if e["type"] == "tool_end"]
            errors_list = [e for e in events if e["type"] == "tool_error"]
            failed_ends = [e for e in ends if e.get("success") is False]
            success_count = len(ends) - len(failed_ends)
            error_count = len(errors_list) + len(failed_ends)
            avg_dur = 0
            if ends:
                avg_dur = sum(e.get("duration_ms", 0) for e in ends) // max(
                    len(ends), 1
                )
            w(
                f"| {tname} | {len(starts)} | {success_count} | {error_count} | {avg_dur}ms |"
            )
        w("")

        sm_events = tool_summary.get("search_memories", [])
        if sm_events:
            sm_starts = [e for e in sm_events if e["type"] == "tool_start"]
            w("### search_memories 调用详情")
            w("")
            w(f"共 {len(sm_starts)} 次记忆搜索调用")
            w("")
            for e in sm_starts:
                preview = str(e.get("input_preview", ""))[:100]
                w(f"- `{preview}`")
            w("")

        hard_errors = [ev for ev in tool_logs if ev.get("type") == "tool_error"]
        soft_fails = [
            ev
            for ev in tool_logs
            if ev.get("type") == "tool_end"
            and ev.get("data", {}).get("success") is False
        ]

        if hard_errors:
            w(f"### 工具错误事件 ({len(hard_errors)} 条)")
            w("")
            w("```json")
            for ev in hard_errors:
                w(json.dumps(ev, ensure_ascii=False, default=str))
            w("```")
            w("")
        else:
            w("**工具错误**: 未发现 tool_error 事件 ✅")
            w("")

        if soft_fails:
            from scripts.conversation_test_lib.analyzers import (
                _build_round_windows,
                _event_epoch,
                _extract_soft_fail_reason,
                _match_round,
            )

            start_inputs = {
                ev.get("data", {}).get("run_id"): ev.get("data", {}).get(
                    "input_preview", ""
                )
                for ev in tool_logs
                if ev.get("type") == "tool_start"
            }
            soft_windows = _build_round_windows(conv_results)
            w(f"### 工具软失败事件 ({len(soft_fails)} 条)")
            w("")
            w(
                "> 工具正常返回但业务 `success=false` (常为模型传参不当, "
                "如把工具组名当 skill 名). 未抛异常, 不计入 server ERROR."
            )
            w("")
            w("| 工具 | 轮次 | 入参 | 失败原因 |")
            w("|------|------|------|---------|")
            for ev in soft_fails:
                d = ev.get("data", {})
                tname = d.get("tool_name", "unknown")
                epoch = _event_epoch(ev)
                rnd = _match_round(epoch, soft_windows) if epoch else None
                rnd_col = f"R{rnd}" if rnd else "—"
                input_col = (
                    _truncate(str(start_inputs.get(d.get("run_id"), "")), 80).replace(
                        "|", "\\|"
                    )
                    or "(无)"
                )
                reason = _extract_soft_fail_reason(d.get("output_preview", ""))
                reason_col = reason.replace("|", "\\|") if reason else "(无详情)"
                w(f"| {tname} | {rnd_col} | {input_col} | {reason_col} |")
            w("")

        llm_events = [
            ev for ev in tool_logs if str(ev.get("type", "")).startswith("llm_")
        ]
        llm_ends = [ev for ev in llm_events if ev.get("type") == "llm_end"]
        llm_errs = [ev for ev in llm_events if ev.get("type") == "llm_error"]
        total_llm_ms = sum(
            int(e.get("data", {}).get("duration_ms", 0) or 0) for e in llm_ends
        )
        w("### LLM 调用概览")
        w("")
        w(
            f"共 {len(llm_ends)} 次成功调用, 累计 {total_llm_ms / 1000:.1f}s "
            f"(主对话 + 专家工具子 Agent)"
        )
        w("")
        if llm_errs:
            w(f"### LLM 错误事件 ({len(llm_errs)} 条)")
            w("")
            w("```json")
            for ev in llm_errs:
                w(json.dumps(ev, ensure_ascii=False, default=str))
            w("```")
            w("")
        else:
            w("**LLM 错误**: 未发现 llm_error 事件 ✅")
            w("")
    else:
        w("*(未找到工具调用日志)*")
        w("")

    # ---- 6. Prompt结构摘要 ----
    w("## 6. Prompt结构摘要")
    w("")
    if prompt_logs:
        has_history_messages = any(p.get("history_messages") for p in prompt_logs)
        path_label = "messages 数组" if has_history_messages else "字符串拼接 (旧格式)"
        w(f"共 {len(prompt_logs)} 个prompt文件 (路径: {path_label})")
        w("")

        w("| # | 时间 | 对话历史 | 轮次 | 索引区 | TODO | 总长 |")
        w("|---|------|---------|------|--------|------|------|")

        overflow_start = None
        for idx, p in enumerate(prompt_logs, 1):
            content = p.get("user_content", "")
            ts_p = p.get("timestamp", "?")[:19]
            history_msgs = p.get("history_messages", [])

            if history_msgs:
                round_info, index_info, _ = _analyze_history_messages(history_msgs)
                conv_icon = "✅"
                if index_info and overflow_start is None:
                    overflow_start = idx
            else:
                conv_section = _extract_section(content, "conversation_history")
                if conv_section:
                    round_count, r_min, r_max = _count_rounds(conv_section)
                    round_info = (
                        f"{round_count}轮 [{r_min}-{r_max}]"
                        if round_count > 0
                        else "0轮"
                    )
                else:
                    round_info = "❌ 缺失"

                if idx == 1 and not conv_section:
                    conv_icon = "➖"
                    round_info = "首轮"
                else:
                    conv_icon = "✅" if conv_section else "❌"

                index_section = (
                    _extract_section(conv_section or "", "index")
                    if conv_section
                    else ""
                )
                if index_section:
                    idx_count, idx_min, idx_max = _count_index_rounds(index_section)
                    index_info = f"✅ {idx_count}轮 [{idx_min}-{idx_max}]"
                    if overflow_start is None:
                        overflow_start = idx
                else:
                    index_info = ""

            todo_section = _extract_section(
                content, "current_todos"
            ) or _extract_section(content, "todo_list")
            if todo_section:
                todo_count = _count_todo_items(todo_section)
                todo_info = f"✅ {todo_count}项"
            else:
                todo_info = ""

            capture_info = p.get("capture_info", {}) or {}
            total_len = len(content) + int(capture_info.get("history_total_length", 0))

            w(
                f"| {idx} | {ts_p} | {conv_icon} | {round_info} | {index_info} | {todo_info} | {total_len} |",
            )
        w("")

        if overflow_start is not None:
            w("### 记忆溢出检测")
            w("")
            w(f"**溢出起始**: Prompt #{overflow_start}")
            w("")
            w(
                f"从第 {overflow_start} 个prompt开始出现索引区, 早期对话被压缩为索引摘要."
            )
            w("")
        else:
            w("### 记忆溢出检测")
            w("")
            w(
                "**未检测到溢出** - 对话未触发记忆溢出 "
                "(属正常情况, 工具调用密集轮次可能产生较长响应)."
            )
            w("")

        anomalies: list[str] = []
        for idx, p in enumerate(prompt_logs[1:], 2):
            content = p.get("user_content", "")
            ts_p = p.get("timestamp", "?")[:19]
            history_msgs = p.get("history_messages", [])

            ctx_section = _extract_section(content, "current_context")
            input_section = _extract_section(content, "user_input")

            has_history = bool(
                _extract_section(content, "conversation_history") or history_msgs,
            )
            if not has_history:
                anomalies.append(f"Prompt {idx} ({ts_p}): 缺少 conversation_history")
            if not ctx_section:
                anomalies.append(f"Prompt {idx} ({ts_p}): 缺少 current_context")
            if not input_section:
                anomalies.append(f"Prompt {idx} ({ts_p}): 缺少 user_input")

        if anomalies:
            w("### 异常提示")
            w("")
            for a in anomalies:
                w(f"- ⚠️ {a}")
            w("")
        else:
            w("**异常检测**: 未发现结构异常 ✅")
            w("")

        req_eval = _evaluate_user_requirement(
            conv_results, db_data.get("pinned_memory", [])
        )
        if req_eval.get("has_requirement_round"):
            w("### 用户要求记录效果评估")
            w("")
            w(f"**要求轮次**: R{req_eval['requirement_round']:02d}")
            w("")
            w("| 指标 | 要求前 | 要求后 | 说明 |")
            w("|------|--------|--------|------|")
            w(
                f"| 平均回复长度 | {req_eval['avg_len_before']} 字 | "
                f"{req_eval['avg_len_after']} 字 | 越短越符合'简洁'要求 |",
            )
            w(
                f"| emoji 出现次数 | {req_eval['emojis_before']} 次 | "
                f"{req_eval['emojis_after']} 次 | 越少越符合'少用 emoji'要求 |",
            )
            captured = "✅ 是" if req_eval["style_captured_in_pinned"] else "❌ 否"
            w(
                f"| 置顶记忆已记录 | — | {captured} | 检查置顶记忆是否包含风格关键词 |",
            )
            w("")
            w(
                "> 注: 新置顶记忆体系下, 用户非一次性要求由主模型写入 `<pinned_memory>`; "
                "效果评估结合置顶记忆内容与后续回复风格变化综合判断."
            )
            w("")
    else:
        w("*(未找到prompt日志)*")
        w("")

    # ---- 7. 错误与警告 ----
    w("## 7. 错误与警告")
    w("")
    if server_errors:
        from scripts.conversation_test_lib.analyzers import (
            _build_round_windows,
            _match_round,
            _server_error_epoch,
        )

        windows = _build_round_windows(conv_results)
        first_lo = windows[0][1] if windows else None
        round_margin = 5.0

        matched: list[tuple[int | None, dict[str, Any]]] = []
        filtered_count = 0
        for ev in server_errors:
            epoch = _server_error_epoch(ev)
            if epoch is None:
                matched.append((None, ev))
                continue
            if first_lo is not None and epoch < first_lo - round_margin:
                filtered_count += 1
                continue
            rnd = _match_round(epoch, windows)
            matched.append((rnd, ev))

        if not matched:
            w("*(未发现ERROR/Traceback日志)*")
            w("")
        else:
            if filtered_count:
                w(
                    f"共 {len(matched)} 个错误事件 ({filtered_count} 个时间线外事件已过滤)"
                )
            else:
                w(f"共 {len(matched)} 个错误事件")
            w("")
            w("```")
            for rnd, ev in matched[:50]:
                prefix = f"[R{rnd:02d}]" if rnd is not None else "[--]"
                w(
                    f"{prefix} {ev['headline'].split('] ', 1)[-1] if '] ' in ev['headline'] else ev['headline']}"
                )
                event_lines = ev.get("lines", [])
                if len(event_lines) <= 10:
                    for line in event_lines[1:]:
                        w(line)
                else:
                    for line in event_lines[1:10]:
                        w(line)
                    w(f"... ({len(event_lines) - 10} 行 traceback 续行已折叠)")
            w("```")
            w("")
    else:
        w("*(未发现ERROR/Traceback日志)*")
        w("")

    # ---- 8. 综合评估 ----
    w("## 8. 综合评估")
    w("")

    checks: list[tuple[str, str]] = []

    failed_rounds = [
        r
        for r in conv_results
        if isinstance(r.get("status_code"), int) and r["status_code"] != 200
    ]
    total = len(conv_results)
    passed = total - len(failed_rounds)
    if not failed_rounds:
        checks.append((f"✅ {passed}/{total} 成功", ""))
    else:
        failed_ids = ", ".join(f"R{r['round']:02d}" for r in failed_rounds)
        checks.append((f"❌ {passed}/{total} 成功", f"失败轮次: {failed_ids}"))

    conv_count = len(db_data.get("conversations", []))
    if conv_count == total:
        checks.append((f"✅ {conv_count} 条记录, 完整", ""))
    else:
        checks.append((
            f"⚠️ {conv_count} 条记录",
            f"预期 {total} 条, 差异 {total - conv_count}",
        ))

    pinned = db_data.get("pinned_memory", [])
    if pinned:
        checks.append((f"✅ {len(pinned)} 条", "见 Ch3 置顶记忆原始内容与演进"))
    else:
        checks.append(("❌ 无记录", ""))

    todos = db_data.get("todos", [])
    if not todos:
        checks.append(("⚠️ 无TODO记录", ""))
    else:
        statuses = {t.get("status", "") for t in todos}
        issues: list[str] = []
        deleted = [t for t in todos if t.get("status") == "DELETED"]
        if deleted:
            issues.append(f"{len(deleted)} 条 DELETED 残留")
        if issues:
            checks.append((f"⚠️ {len(todos)} 条 TODO", "; ".join(issues)))
        else:
            checks.append((f"✅ {len(todos)} 条 TODO", f"状态: {','.join(statuses)}"))

    eval_hard: list[str] = []
    eval_soft: list[str] = []
    for ev in tool_logs:
        t = ev.get("type")
        if t == "tool_error":
            eval_hard.append(ev.get("data", {}).get("tool_name", "unknown"))
        elif t == "tool_end" and ev.get("data", {}).get("success") is False:
            eval_soft.append(ev.get("data", {}).get("tool_name", "unknown"))
    total_fail = len(eval_hard) + len(eval_soft)
    if total_fail == 0:
        checks.append(("✅ 无 tool_error", ""))
    else:
        parts = []
        if eval_hard:
            parts.append(f"{len(eval_hard)} 硬错误({','.join(sorted(set(eval_hard)))})")
        if eval_soft:
            parts.append(f"{len(eval_soft)} 软失败({','.join(sorted(set(eval_soft)))})")
        checks.append((f"❌ {total_fail} 个错误", " + ".join(parts)))

    if prompt_logs:
        missing_conv = sum(
            1
            for p in prompt_logs[1:]
            if not _extract_section(p.get("user_content", ""), "conversation_history")
            and not p.get("history_messages")
        )
        if missing_conv == 0:
            checks.append(("✅ Prompt结构正常", ""))
        else:
            checks.append((f"⚠️ {missing_conv} 个prompt缺少conversation_history", ""))
    else:
        checks.append(("⚠️ 无prompt日志", ""))

    req_eval = _evaluate_user_requirement(
        conv_results, db_data.get("pinned_memory", [])
    )
    if req_eval.get("has_requirement_round"):
        details: list[str] = []
        if req_eval["style_captured_in_pinned"]:
            details.append("置顶记忆已记录")
        else:
            details.append("置顶记忆未记录")
        details.append(
            f"回复长度 {req_eval['avg_len_before']}→{req_eval['avg_len_after']} 字"
        )
        details.append(
            f"emoji {req_eval['emojis_before']}→{req_eval['emojis_after']} 次"
        )
        checks.append(("✅ 已评估", "; ".join(details)))
    else:
        checks.append(("➖ 无用户要求记录轮", ""))

    if not server_errors:
        checks.append(("✅ 无 ERROR/Traceback", ""))
    else:
        event_count = len(server_errors)
        total_lines = sum(len(e.get("lines", [])) for e in server_errors)
        checks.append((f"❌ {event_count} 个错误事件 ({total_lines} 行)", ""))

    att_unreg = att_result["unregistered"]
    att_ok = att_result["ok"]
    att_missing = att_result["missing"]
    if att_unreg:
        detail = "; ".join(
            f"{it['label']}: {', '.join(it['fake_ids'])}" for it in att_unreg
        )
        checks.append((f"❌ {len(att_unreg)} 个未注册附件标记(疑似幻觉)", detail))
    elif not att_ok and not att_missing:
        checks.append(("➖ 无文件产出轮次", ""))
    elif not att_missing:
        checks.append((f"✅ {len(att_ok)} 轮已注入附件链接", ""))
    else:
        detail = f"缺链接: {', '.join(att_missing)}"
        checks.append((f"⚠️ {len(att_missing)} 轮缺附件链接", detail))

    w("| # | 检查项 | 结果 | 详情 |")
    w("|---|--------|------|------|")
    labels = [
        "对话响应",
        "数据库记录",
        "置顶记忆",
        "TODO生命周期",
        "工具调用",
        "Prompt结构",
        "用户要求遵循",
        "服务日志",
        "附件链接",
    ]
    for i, (label, (result, detail)) in enumerate(zip(labels, checks), 1):
        detail_col = detail.replace("|", "\\|") if detail else ""
        w(f"| {i} | {label} | {result} | {detail_col} |")
    w("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return report_path
