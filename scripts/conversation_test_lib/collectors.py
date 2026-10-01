"""数据库、工具日志、Prompt 日志与服务日志采集."""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.conversation_test_lib.config import ConversationTestConfig

# 服务日志行首时间戳: "2026-06-23 17:32:14,192" (本地时间, 与 datetime.now() 一致)
_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")

# kb_read_image 直注短路行 (KbImageInjectMiddleware): 图片本体直注主对话,
# 工具不执行 → tracker 无事件, 报告工具统计需从该行补齐
_INJECT_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    r".*?_image_inject.*?知识库图片直注主对话: (\S+) \((\d+) bytes\)"
)


def _read_sqlite(db_path: Path, query: str) -> list[dict[str, Any]]:
    """执行 SQLite 查询并返回字典列表."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(query).fetchall()]
        conn.close()
        return rows
    except Exception as e:
        return [{"error": str(e)}]


def collect_usage_stats(session_start: float, user_id: str) -> dict[str, Any]:
    """从 usage.db 读取本 session 精确 token 用量.

    用时间窗 [session_start, now] 隔离本轮 (二者均转 UTC, 与 created_at 对齐).
    """
    db_path = Path(f"data/{user_id}/database/usage.db")
    if not db_path.exists():
        return {"available": False, "rows": [], "reason": "usage.db 不存在"}
    lo = datetime.fromtimestamp(session_start, UTC).strftime("%Y-%m-%d %H:%M:%S")
    hi = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT usage_source, provider, model_id,
                       SUM(COALESCE(input_tokens, 0)) AS in_tok,
                       SUM(COALESCE(output_tokens, 0)) AS out_tok,
                       SUM(COALESCE(reasoning_tokens, 0)) AS reason_tok,
                       SUM(COALESCE(total_tokens, 0)) AS total_tok,
                       COUNT(*) AS calls
                FROM usage_records
                WHERE created_at >= ? AND created_at <= ?
                GROUP BY usage_source, provider, model_id
                ORDER BY usage_source, total_tok DESC
                """,
                (lo, hi),
            ).fetchall()
        ]
        conn.close()
    except Exception as e:
        return {"available": False, "rows": [], "reason": str(e)}
    return {"available": True, "rows": rows, "window": (lo, hi)}


def collect_db_data(config: ConversationTestConfig) -> dict[str, Any]:
    """采集 agent 级与用户级数据库内容."""
    db_dir = config.data_dir / "database"
    vector_dir = config.data_dir / "vector"
    user_db_dir = Path(f"data/{config.user_id}/database")

    data: dict[str, Any] = {}

    conv_rows = _read_sqlite(
        db_dir / "conversation_history.db",
        "SELECT round_number, created_at, user_message, assistant_response "
        "FROM conversation_index ORDER BY round_number",
    )
    data["conversations"] = conv_rows

    attachment_rows = _read_sqlite(
        user_db_dir / "file_registry.db",
        "SELECT file_id, file_type, filename, round_number, brief "
        "FROM file_registry ORDER BY round_number",
    )
    data["attachments"] = attachment_rows
    data["attachment_ids"] = {
        row["file_id"] for row in attachment_rows if row.get("file_id")
    }

    index_group_rows = _read_sqlite(
        db_dir / "conversation_history.db",
        "SELECT round_start, round_end, arc_phrase "
        "FROM conversation_index_group ORDER BY round_start",
    )
    data["index_groups"] = index_group_rows

    pinned_rows = _read_sqlite(
        db_dir / "pinned_memory.db",
        "SELECT content, updated_at FROM pinned_memory_block ORDER BY updated_at DESC",
    )
    data["pinned_memory"] = pinned_rows

    # todo.db 与 file_registry.db 同为用户级库 (TODO 三级隔离迁移后)
    todo_rows = _read_sqlite(
        user_db_dir / "todo.db",
        "SELECT id, title, description, status, priority, created_at, updated_at "
        "FROM todo_items WHERE status != 'DELETED' ORDER BY created_at",
    )
    data["todos"] = todo_rows

    chroma_path = vector_dir / "chroma.sqlite3"
    if chroma_path.exists():
        data["vector"] = {
            "chroma_size": chroma_path.stat().st_size,
            "chroma_size_human": f"{chroma_path.stat().st_size / 1024:.1f} KB",
        }
        try:
            vrows = _read_sqlite(
                chroma_path,
                "SELECT count(*) as cnt FROM collections",
            )
            data["vector"]["collections_count"] = vrows[0]["cnt"] if vrows else 0
            emb_rows = _read_sqlite(
                chroma_path, "SELECT count(*) as cnt FROM embeddings"
            )
            data["vector"]["embeddings_count"] = emb_rows[0]["cnt"] if emb_rows else 0
        except Exception as e:
            data["vector"]["error"] = str(e)
    else:
        data["vector"] = {"status": "not_found"}

    return data


def collect_tool_call_logs(
    session_start_dt: datetime,
    session_start: float,
    logs_dir: Path,
    extra_log_paths: list[Path] | None = None,
) -> list[dict[str, Any]]:
    """采集工具调用事件 (tool_calls_*.json + 服务日志直注短路事件).

    直注事件来自 KbImageInjectMiddleware 短路的 kb_read_image 调用,
    工具本体未执行 → tracker 无记录, 从服务日志直注行合成补齐.
    """
    logs: list[dict[str, Any]] = []
    if logs_dir.exists():
        for f in sorted(logs_dir.glob("tool_calls_*.json")):
            if f.stat().st_mtime < session_start:
                continue
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    with contextlib.suppress(json.JSONDecodeError):
                        logs.append(json.loads(line))
    logs.extend(
        collect_direct_inject_events(session_start_dt, logs_dir, extra_log_paths)
    )
    return logs


def collect_prompt_logs(
    session_start: float, config: ConversationTestConfig
) -> list[dict[str, Any]]:
    """采集本线程的 prompt 日志 (prompts/prompt_*.json)."""
    prompts_dir = config.logs_dir / "prompts"
    logs: list[dict[str, Any]] = []
    if not prompts_dir.exists():
        return logs

    for f in sorted(prompts_dir.glob("prompt_*.json")):
        if f.stat().st_mtime < session_start:
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, Exception):
            continue
        if (
            data.get("user_id") != config.user_id
            or data.get("thread_id") != config.thread_id
        ):
            continue
        logs.append(data)
    return logs


def _is_tracker_info_noise(line: str) -> bool:
    """tool_call_tracker 的 INFO 行不应被识别为错误."""
    return "tool_call_tracker" in line and "- INFO -" in line


def _is_server_instance_log(name: str) -> bool:
    """是否 dev_server 实例日志 (server_{port}.log)."""
    return name.startswith("server_") and name.endswith(".log")


def collect_server_logs(
    session_start_dt: datetime,
    session_start: float,
    logs_dir: Path,
    extra_log_paths: list[Path] | None = None,
) -> list[dict[str, Any]]:
    """采集本轮 session 的服务日志 ERROR/Traceback, 按错误事件聚合.

    按行首时间戳过滤, 跨轮次追加写入的日志文件不会泄漏历史错误.
    """
    events: list[dict[str, Any]] = []

    scan_dirs: list[Path] = []
    if logs_dir.exists():
        scan_dirs.append(logs_dir)
    for p in extra_log_paths or []:
        if p.exists():
            scan_dirs.append(p)

    for scan_dir in scan_dirs:
        files = sorted(scan_dir.glob("*.log") if scan_dir.is_dir() else [scan_dir])
        for f in files:
            if scan_dir == logs_dir and not _is_server_instance_log(f.name):
                continue
            if f.stat().st_mtime < session_start:
                continue
            current_in_window = False
            current_event: dict[str, Any] | None = None
            with open(f, encoding="utf-8", errors="replace") as fh:
                for raw_line in fh:
                    stripped = raw_line.rstrip("\n")
                    m = _LOG_TS_RE.match(stripped)
                    if m:
                        if current_event is not None:
                            events.append(current_event)
                            current_event = None
                        try:
                            entry_dt = datetime.strptime(
                                m.group(1), "%Y-%m-%d %H:%M:%S"
                            )
                        except ValueError:
                            entry_dt = None
                        current_in_window = (
                            entry_dt is not None and entry_dt >= session_start_dt
                        )
                        upper = stripped.upper()
                        if (
                            current_in_window
                            and not any(
                                tag in upper
                                for tag in ("- WARNING -", "- INFO -", "- DEBUG -")
                            )
                            and (
                                "ERROR" in upper
                                or "TRACEBACK" in upper
                                or "❌" in stripped
                            )
                            and not _is_tracker_info_noise(stripped)
                        ):
                            current_event = {
                                "headline": f"[{f.name}] {stripped}",
                                "source_file": f.name,
                                "timestamp": m.group(1),
                                "lines": [f"[{f.name}] {stripped}"],
                            }
                    elif current_in_window and current_event is not None:
                        current_event["lines"].append(f"[{f.name}] {stripped}")
            if current_event is not None:
                events.append(current_event)

    # 去重: 同一事件可能出现在多个日志文件中.
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for ev in events:
        key = (
            ev["timestamp"],
            ev["headline"].split("] ", 1)[-1]
            if "] " in ev["headline"]
            else ev["headline"],
        )
        if key not in seen:
            seen.add(key)
            unique.append(ev)
    return unique


def _iter_scan_files(
    logs_dir: Path, extra_log_paths: list[Path] | None
) -> list[tuple[Path, bool]]:
    """汇总服务日志扫描文件: logs_dir 内 server_*.log + 显式追加路径(文件或目录)."""
    files: list[tuple[Path, bool]] = []
    if logs_dir.exists():
        for f in sorted(logs_dir.glob("*.log")):
            if _is_server_instance_log(f.name):
                files.append((f, True))
    for p in extra_log_paths or []:
        if p.is_dir():
            files.extend((f, False) for f in sorted(p.glob("*.log")))
        elif p.exists():
            files.append((p, False))
    return files


def collect_direct_inject_events(
    session_start_dt: datetime,
    logs_dir: Path,
    extra_log_paths: list[Path] | None = None,
) -> list[dict[str, Any]]:
    """采集 kb_read_image 直注短路事件, 合成 tracker 兼容事件对.

    多模态主模型下 KbImageInjectMiddleware 短路工具本体 (原图直注主对话),
    tool_call_tracker 无 tool_start/tool_end → 报告工具统计漏计该调用;
    从服务日志 "🖼️ 知识库图片直注主对话" 行补齐, 每行对应一次调用.
    """
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for f, _ in _iter_scan_files(logs_dir, extra_log_paths):
        with open(f, encoding="utf-8", errors="replace") as fh:
            for stripped in (line.rstrip("\n") for line in fh):
                m = _INJECT_LINE_RE.match(stripped)
                if not m:
                    continue
                ts_local, image_ref, size = m.groups()
                try:
                    entry_dt = datetime.strptime(ts_local, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if entry_dt < session_start_dt:
                    continue
                key = (ts_local, image_ref)
                if key in seen:
                    continue
                seen.add(key)
                ts_utc = datetime.fromtimestamp(
                    entry_dt.timestamp(), tz=UTC
                ).isoformat()
                events.extend([
                    {
                        "ts": ts_utc,
                        "type": "tool_start",
                        "data": {
                            "tool_name": "kb_read_image",
                            "input_preview": f"{{'image_ref': '{image_ref}'}}",
                        },
                    },
                    {
                        "ts": ts_utc,
                        "type": "tool_end",
                        "data": {
                            "tool_name": "kb_read_image",
                            "duration_ms": 0,
                            "success": True,
                            "output_preview": (
                                f"原图直注主对话 ({size} bytes, 多模态短路, 无视觉转述)"
                            ),
                        },
                    },
                ])
    return events
