"""报告所需的纯分析函数."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from scripts.conversation_test_lib.formatting import _truncate

# 常见 emoji Unicode 范围, 用于评估用户"少用 emoji"要求是否生效
_EMOJI_RE = re.compile(
    r"["
    r"\U0001F600-\U0001F64F"  # 表情
    r"\U0001F300-\U0001F5FF"  # 符号/象形
    r"\U0001F680-\U0001F6FF"  # 交通/地图
    r"\U0001F1E0-\U0001F1FF"  # 国旗
    r"\U00002702-\U000027B0"  # 杂项符号
    r"\U000024C2-\U0001F251"  # 补充符号
    r"]+",
    flags=re.UNICODE,
)

# 应产出附件(系统生成文件)的轮次 tag 关键词
_FILE_PRODUCING_TAGS = ("图片生成", "文档导出", "图表生成", "Excel报表生成")

# Markdown 链接与文件下载 URL 模式
_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_FILE_DOWNLOAD_URL_RE = re.compile(r"https?://[^\s\"')]+/v1/files/dl/[^\s\"')]+")

# [file: file_id] 标记
_ATTACHMENT_MARK_RE = re.compile(r"\[file\s*[:：]\s*([0-9a-fA-F]+)\s*\]")

# scnet 聚合模型名 (API 返回的裸名, 无 provider 前缀).
_SCNET_MODEL_NAMES = {"Kimi-K2.6", "MiniMax-M3", "MiMo-V2.5-Pro", "GLM-5.2"}


def _is_scnet_model(model_id: str | None) -> bool:
    """判断 model_id 是否为 scnet 聚合模型."""
    if not model_id:
        return False
    if model_id in _SCNET_MODEL_NAMES:
        return True
    return any(model_id.endswith(f":{name}") for name in _SCNET_MODEL_NAMES)


def _extract_section(content: str, tag: str) -> str:
    """提取 XML 标签内容, 如 <tag>...</tag>."""
    start_tag = f"<{tag}>"
    end_tag = f"</{tag}>"
    start_idx = content.find(start_tag)
    if start_idx < 0:
        return ""
    end_idx = content.find(end_tag, start_idx + len(start_tag))
    if end_idx < 0:
        return ""
    return content[start_idx + len(start_tag) : end_idx].strip()


def _count_rounds(text: str) -> tuple[int, int, int]:
    """统计 [Round N] 标记, 返回 (数量, 最小轮次, 最大轮次)."""
    matches = re.findall(r"\[Round (\d+)\]", text)
    if not matches:
        return 0, 0, 0
    nums = [int(m) for m in matches]
    return len(nums), min(nums), max(nums)


def _count_todo_items(text: str) -> int:
    """统计 TODO 事项数量, 按 - [#N] 模式匹配."""
    return len(re.findall(r"- \[#\d+\]", text))


def _count_index_rounds(text: str) -> tuple[int, int, int]:
    """统计索引区轮次 (markdown 表格中的 | N | 行)."""
    matches = re.findall(r"\|\s*(\d+)\s*\|", text)
    if not matches:
        return 0, 0, 0
    nums = [int(m) for m in matches if 0 < int(m) < 10000]
    if not nums:
        return 0, 0, 0
    return len(nums), min(nums), max(nums)


def _count_arc_rows(text: str) -> int:
    """统计 <timeline> 弧短语行数 (排除表头与分隔行)."""
    if not text:
        return 0
    content_rows = 0
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        if re.fullmatch(r"[\s|:-]+", s):
            continue
        content_rows += 1
    return max(content_rows - 1, 0)


def _analyze_index_zones(content: str) -> tuple[int, int]:
    """从含 <conversation_index> 的内容里统计双区, 返回 (弧短语数, bridge 轮数)."""
    timeline_section = _extract_section(content, "timeline")
    arc_count = _count_arc_rows(timeline_section)
    index_section = _extract_section(content, "index")
    bridge_count, _, _ = _count_index_rounds(index_section)
    return arc_count, bridge_count


def _analyze_history_messages(
    history_msgs: list[dict[str, Any]],
) -> tuple[str, str, bool]:
    """分析 history_messages 数组, 返回 (轮次信息, 索引区信息, 是否有效)."""
    if not history_msgs:
        return "0轮", "", False

    human_count = sum(1 for m in history_msgs if m.get("type") == "human")
    has_index_pseudo = any(
        m.get("type") == "human"
        and str(m.get("content", "")).startswith("[过往对话回顾]")
        for m in history_msgs
    )
    if not has_index_pseudo:
        has_index_pseudo = any(
            "<conversation_index>" in str(m.get("content", ""))
            for m in history_msgs
            if m.get("type") == "ai"
        )

    real_rounds = human_count - (1 if has_index_pseudo else 0)
    round_info = f"{real_rounds}轮"
    if has_index_pseudo:
        arc_count, bridge_count = 0, 0
        for m in history_msgs:
            if m.get("type") == "ai" and "<conversation_index>" in str(
                m.get("content", ""),
            ):
                arc_count, bridge_count = _analyze_index_zones(
                    str(m.get("content", "")),
                )
                break
        parts: list[str] = []
        if arc_count:
            parts.append(f"弧{arc_count}")
        if bridge_count:
            parts.append(f"bridge {bridge_count}轮")
        zone_str = f" ({'/'.join(parts)})" if parts else ""
        index_info = f"✅ 索引区{zone_str}"
    else:
        index_info = ""
    return round_info, index_info, True


def _check_attachment_markers(
    conv_results: list[dict[str, Any]],
    real_ids: set[str] | None = None,
) -> dict[str, list]:
    """检查文件产出轮次的响应是否含真实可下载附件, 并交叉核验附件标记."""
    real_ids = real_ids or set()
    ok: list[str] = []
    missing: list[str] = []
    unregistered: list[dict[str, Any]] = []
    for r in conv_results:
        tag = r.get("tag", "")
        resp = r.get("response")
        content = ""
        if isinstance(resp, dict):
            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        content_str = str(content)
        label = f"R{r['round']:02d}({tag})"

        marks = _ATTACHMENT_MARK_RE.findall(content_str)
        fake_ids = sorted({fid for fid in marks if fid not in real_ids})
        if fake_ids:
            unregistered.append({"label": label, "fake_ids": fake_ids})

        if not any(kw in tag for kw in _FILE_PRODUCING_TAGS):
            continue
        has_markdown_link = bool(_MARKDOWN_LINK_RE.search(content_str))
        has_download_url = bool(_FILE_DOWNLOAD_URL_RE.search(content_str))
        has_real_mark = any(fid in real_ids for fid in marks)
        if has_markdown_link or has_download_url or has_real_mark:
            ok.append(label)
        else:
            missing.append(label)
    return {"ok": ok, "missing": missing, "unregistered": unregistered}


def _parse_epoch(ts: object) -> float | None:
    """把时间戳 (UTC iso 字符串, 含空格的 SQLite created_at) 转为 epoch 秒."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _event_epoch(ev: dict[str, Any]) -> float | None:
    """把事件 ts (UTC iso) 转为 epoch 秒."""
    return _parse_epoch(ev.get("ts"))


def _server_error_epoch(ev: dict[str, Any]) -> float | None:
    """把 server_error 的 naive local timestamp 转为 epoch 秒."""
    ts = ev.get("timestamp")
    if not ts:
        return None
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _match_round(epoch: float, windows: list[tuple[int, float, float]]) -> int | None:
    """将 epoch 秒匹配到轮次时间窗 [lo, hi), 返回轮号或 None."""
    for rnd, lo, hi in windows:
        if lo <= epoch < hi:
            return rnd
    return None


def _extract_tokens(resp: object) -> tuple[int, int]:
    """从 OpenAI 格式响应提取 (prompt_tokens, completion_tokens)."""
    if not isinstance(resp, dict):
        return 0, 0
    usage = resp.get("usage") or {}
    try:
        return int(usage.get("prompt_tokens", 0) or 0), int(
            usage.get("completion_tokens", 0) or 0,
        )
    except (TypeError, ValueError):
        return 0, 0


def _build_round_windows(
    conv_results: list[dict[str, Any]],
    db_conversations: list[dict[str, Any]] | None = None,
) -> list[tuple[int, float, float]]:
    """构建每轮的连续半开时间窗 [lo, hi)."""
    starts = sorted(
        (int(r["round"]), float(r.get("start_ts") or 0))
        for r in conv_results
        if r.get("start_ts")
    )
    if starts:
        windows: list[tuple[int, float, float]] = []
        for idx, (rnd, lo) in enumerate(starts):
            hi = starts[idx + 1][1] if idx + 1 < len(starts) else float("inf")
            windows.append((rnd, lo, hi))
        return windows

    if not db_conversations:
        return []

    ends = sorted(
        (int(r["round_number"]), _parse_epoch(r.get("created_at")))
        for r in db_conversations
        if r.get("created_at")
    )
    windows = []
    for idx, (rnd, hi) in enumerate(ends):
        lo = ends[idx - 1][1] if idx > 0 else hi - 300
        windows.append((rnd, lo, hi))
    if windows:
        rnd, lo, _ = windows[-1]
        windows[-1] = (rnd, lo, float("inf"))
    return windows


def _bucket_events_by_round(
    events: list[dict[str, Any]],
    windows: list[tuple[int, float, float]],
) -> dict[int, list[dict[str, Any]]]:
    """按时间窗把事件分桶到轮次."""
    bucketed: dict[int, list[dict[str, Any]]] = {rnd: [] for rnd, _, _ in windows}
    for ev in events:
        epoch = _event_epoch(ev)
        if epoch is None:
            continue
        for rnd, lo, hi in windows:
            if lo <= epoch < hi:
                bucketed[rnd].append(ev)
                break
    return bucketed


def _format_tool_sequence(starts: list[dict[str, Any]]) -> str:
    """把一轮内的 tool_start 事件渲染为紧凑序列."""
    if not starts:
        return ""
    ordered = sorted(starts, key=lambda e: _event_epoch(e) or 0.0)
    groups: list[list[str]] = []
    parent_index: dict[str, int] = {}
    for e in ordered:
        data = e.get("data", {})
        parent = data.get("parent_run_id") or data.get("run_id") or "?"
        name = data.get("tool_name", "?")
        if parent in parent_index:
            groups[parent_index[parent]].append(name)
        else:
            parent_index[parent] = len(groups)
            groups.append([name])
    return " → ".join("+".join(g) for g in groups)


def _extract_soft_fail_reason(output_preview: str) -> str:
    """从工具软失败的 output_preview 提取可读失败原因."""
    if not output_preview:
        return ""
    stripped = str(output_preview).strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                msg = (
                    payload.get("message")
                    or payload.get("error")
                    or payload.get("detail")
                )
                if msg:
                    avail = payload.get("available_skills") or payload.get(
                        "available_references"
                    )
                    if avail:
                        return f"{msg} (可用: {avail})"
                    return str(msg)
        except (json.JSONDecodeError, ValueError):
            pass
    return _truncate(stripped, 120)


def _enrich_rounds(
    conv_results: list[dict[str, Any]],
    tool_logs: list[dict[str, Any]],
    db_conversations: list[dict[str, Any]] | None = None,
) -> dict[int, dict[str, Any]]:
    """计算每轮富化指标: LLM 调用数/延迟/错误, 工具调用数/序列."""
    windows = _build_round_windows(conv_results, db_conversations)
    llm_events = [ev for ev in tool_logs if str(ev.get("type", "")).startswith("llm_")]
    tool_events = [
        ev
        for ev in tool_logs
        if ev.get("type") in ("tool_start", "tool_end", "tool_error")
    ]
    llm_by_round = _bucket_events_by_round(llm_events, windows)
    tool_by_round = _bucket_events_by_round(tool_events, windows)

    all_rounds = {int(r["round"]) for r in conv_results}
    enrich: dict[int, dict[str, Any]] = {}
    for rnd in sorted(all_rounds):
        llm_ev = llm_by_round.get(rnd, [])
        tl_ev = tool_by_round.get(rnd, [])
        starts = [e for e in tl_ev if e.get("type") == "tool_start"]
        llm_ends = [e for e in llm_ev if e.get("type") == "llm_end"]
        enrich[rnd] = {
            "llm_calls": len([e for e in llm_ev if e.get("type") == "llm_start"]),
            "llm_ms": sum(
                int(e.get("data", {}).get("duration_ms", 0) or 0) for e in llm_ends
            ),
            "llm_errors": [e for e in llm_ev if e.get("type") == "llm_error"],
            "tool_calls": len(starts),
            "tool_sequence": _format_tool_sequence(starts),
        }
    return enrich


def _extract_pinned_block(system_prompt: str) -> str:
    """从 system_prompt 提取 <pinned_memory> 块内容."""
    if not system_prompt:
        return ""
    i = system_prompt.find("<pinned_memory>")
    j = system_prompt.find("</pinned_memory>")
    if i < 0 or j < 0:
        return ""
    return system_prompt[i + len("<pinned_memory>") : j].strip()


def _assistant_content(response: object) -> str:
    """从 conv_results 中的 response 提取助手文本内容."""
    if isinstance(response, dict):
        return (
            response.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        )
    return ""


def _evaluate_user_requirement(
    conv_results: list[dict[str, Any]],
    pinned_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """评估"用户要求记录"轮次的效果."""
    req_round = None
    for r in conv_results:
        if r.get("tag") == "用户要求记录":
            req_round = int(r["round"])
            break

    result: dict[str, Any] = {"has_requirement_round": req_round is not None}
    if req_round is None:
        return result

    before = [r for r in conv_results if int(r["round"]) < req_round]
    after = [r for r in conv_results if int(r["round"]) > req_round]

    before_contents = [_assistant_content(r.get("response")) for r in before]
    after_contents = [_assistant_content(r.get("response")) for r in after]
    before_contents = [c for c in before_contents if c]
    after_contents = [c for c in after_contents if c]

    avg_before = (
        sum(len(c) for c in before_contents) / len(before_contents)
        if before_contents
        else 0.0
    )
    avg_after = (
        sum(len(c) for c in after_contents) / len(after_contents)
        if after_contents
        else 0.0
    )
    emojis_before = sum(len(_EMOJI_RE.findall(c)) for c in before_contents)
    emojis_after = sum(len(_EMOJI_RE.findall(c)) for c in after_contents)

    pinned_text = "\n".join(str(p.get("content", "")) for p in pinned_rows).lower()
    style_captured = any(
        kw in pinned_text for kw in ["简洁", "emoji", "重点", "少用", "直接"]
    )

    result.update({
        "requirement_round": req_round,
        "before_count": len(before_contents),
        "after_count": len(after_contents),
        "avg_len_before": round(avg_before, 1),
        "avg_len_after": round(avg_after, 1),
        "emojis_before": emojis_before,
        "emojis_after": emojis_after,
        "style_captured_in_pinned": style_captured,
    })
    return result


def _prompt_epoch(p: dict[str, Any]) -> float | None:
    """prompt 文件 timestamp (naive local iso) -> epoch 秒."""
    ts = (p.get("metadata", {}) or {}).get("processing_start") or p.get("timestamp")
    if not ts:
        return None
    return _parse_epoch(ts)


def _pinned_line_diff(prev: str, cur: str) -> tuple[list[str], list[str]]:
    """置顶记忆块行级 diff: 返回 (added, removed)."""

    def _lines(s: str) -> list[str]:
        return [ln.strip() for ln in s.splitlines() if ln.strip()]

    prev_lines, cur_lines = _lines(prev), _lines(cur)
    prev_set = set(prev_lines)
    cur_set = set(cur_lines)
    added = [ln for ln in cur_lines if ln not in prev_set]
    removed = [ln for ln in prev_lines if ln not in cur_set]
    return added, removed


def _pinned_evolution(
    conv_results: list[dict[str, Any]],
    prompt_logs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """计算每轮置顶记忆块及与上一轮的 diff."""
    windows = _build_round_windows(conv_results)
    prompt_by_round: dict[int, dict[str, Any]] = {}
    for p in prompt_logs:
        epoch = _prompt_epoch(p)
        if epoch is None:
            continue
        for rnd, lo, hi in windows:
            if lo <= epoch < hi:
                prompt_by_round[rnd] = p
                break

    entries: list[dict[str, Any]] = []
    prev_block = ""
    for rnd, _, _ in windows:
        p = prompt_by_round.get(rnd)
        if p is None:
            continue
        block = _extract_pinned_block(p.get("system_prompt", ""))
        added, removed = _pinned_line_diff(prev_block, block)
        entries.append({
            "round": rnd,
            "present": bool(block),
            "block": block,
            "added": added,
            "removed": removed,
        })
        prev_block = block
    return entries
