#!/usr/bin/env python3
"""对话测试脚本 - Personal Assistant 统一版 (薄入口).

核心逻辑已迁移到 scripts.conversation_test_lib, 本文件仅保留 CLI 入口与
编排流程, 保证原有调用方式不变.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

# 当以 `python scripts/conversation_test.py` 直接运行时, 确保项目根目录在
# sys.path 中, 从而能绝对导入 scripts 包.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.conversation_test_lib.client import check_server, run_conversations
from scripts.conversation_test_lib.collectors import (
    collect_db_data,
    collect_prompt_logs,
    collect_server_logs,
    collect_tool_call_logs,
)
from scripts.conversation_test_lib.config import build_config, parse_args
from scripts.conversation_test_lib.formatting import SEPARATOR, _cyan, _green
from scripts.conversation_test_lib.report import generate_report


def main() -> None:
    """对话测试主流程."""
    args = parse_args()
    config = build_config(args)

    mode_label = "60轮完整版" if args.all else "24轮精简版"
    print(_cyan(SEPARATOR))
    print(_cyan(f"  Personal Assistant 对话测试脚本 ({mode_label})"))
    print(_cyan(f"  Agent: {config.agent_id}  对话轮数: {len(config.conversations)}"))
    print(_cyan(SEPARATOR))

    if not check_server(config):
        sys.exit(1)

    session_start = time.time()
    # naive local, 与服务日志行首时间戳同时区, 用于按行隔离本轮错误
    session_start_dt = datetime.now()

    conv_results = run_conversations(
        config,
        start_round=args.start_round,
        max_rounds=args.max_rounds,
    )

    print(f"\n{_cyan('[采集]')} 正在采集数据库内容...")
    db_data = collect_db_data(config)
    print(f"  - 对话记录: {len(db_data.get('conversations', []))} 条")
    print(f"  - 置顶记忆: {len(db_data.get('pinned_memory', []))} 条")
    print(f"  - TODO: {len(db_data.get('todos', []))} 条")
    vec = db_data.get("vector", {})
    print(f"  - 向量: {vec.get('chroma_size_human', '未找到')}")

    print(f"\n{_cyan('[采集]')} 正在解析工具调用日志...")
    tool_logs = collect_tool_call_logs(session_start, config.logs_dir)
    tool_names = set()
    for ev in tool_logs:
        if ev.get("type") in ("tool_start", "tool_end", "tool_error"):
            tool_names.add(ev.get("data", {}).get("tool_name", ""))
    print(f"  - 事件数: {len(tool_logs)}")
    print(f"  - 涉及工具: {', '.join(sorted(tool_names)) if tool_names else '无'}")

    print(f"\n{_cyan('[采集]')} 正在解析Prompt日志...")
    prompt_logs = collect_prompt_logs(session_start, config)
    print(f"  - Prompt文件: {len(prompt_logs)} 个")

    print(f"\n{_cyan('[采集]')} 正在扫描服务日志错误...")
    server_errors = collect_server_logs(
        session_start_dt, session_start, config.logs_dir, args.server_log
    )
    if server_errors:
        event_count = len(server_errors)
        total_lines = sum(len(e.get("lines", [])) for e in server_errors)
        print(f"  - ERROR/Traceback: {event_count} 个错误事件 ({total_lines} 行)")
    else:
        print("  - ERROR/Traceback: 0 个错误事件")

    print(f"\n{_cyan('[报告]')} 正在生成报告...")
    report_path = generate_report(
        conv_results,
        db_data,
        tool_logs,
        prompt_logs,
        server_errors,
        config,
        session_start,
    )
    print(f"{_green('[完成]')} 报告已保存: {report_path}")
    print(f"\n{SEPARATOR}")


if __name__ == "__main__":
    main()
