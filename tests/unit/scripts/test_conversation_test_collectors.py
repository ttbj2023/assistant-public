"""conversation_test 采集器 (collectors) 的单元测试.

验证 TODO 用户级数据库迁移后, collect_db_data 从用户级目录采集 todo 记录
(而非线程级 agent 目录, 迁移前的旧位置).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from scripts.conversation_test_lib.collectors import collect_db_data
from scripts.conversation_test_lib.config import ConversationTestConfig


def _create_todo_db(path: Path, title: str) -> None:
    """在指定路径创建含一条 ACTIVE 记录的 todo.db."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE todo_items (
            id INTEGER PRIMARY KEY,
            title TEXT,
            description TEXT,
            status TEXT,
            priority TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO todo_items (title, description, status, priority,"
        " created_at, updated_at) VALUES (?, ?, 'ACTIVE', 'high',"
        " '2026-09-15 11:00:00', '2026-09-15 11:00:00')",
        (title, "test description"),
    )
    conn.commit()
    conn.close()


class TestCollectDbDataTodoLocation:
    """collect_db_data 对用户级 todo.db 的采集行为."""

    def test_reads_todo_from_user_level_db(self, tmp_path, monkeypatch) -> None:
        """todo.db 位于用户级目录时被采集, 线程级旧位置不产生记录."""
        monkeypatch.chdir(tmp_path)
        user_id = "testuser"
        _create_todo_db(
            tmp_path / "data" / user_id / "database" / "todo.db",
            "写周报",
        )

        config = ConversationTestConfig(
            user_id=user_id,
            thread_id="main",
            agent_id="agentx",
            data_dir=Path(f"data/{user_id}/main/agentx"),
        )

        data = collect_db_data(config)

        assert len(data["todos"]) == 1
        assert data["todos"][0]["title"] == "写周报"
        assert data["todos"][0]["status"] == "ACTIVE"


_INJECT_LINE = (
    "2026-09-15 21:52:56,619 - src.tools.middleware._image_inject - INFO - "
    "🖼️ 知识库图片直注主对话: tea:中国茶经/images/page0806_174.jpg (195959 bytes)"
)


class TestCollectDirectInjectEvents:
    """collect_direct_inject_events 对 kb_read_image 直注短路行的采集行为."""

    def test_collect_direct_inject_events_窗口内直注行_合成tracker兼容事件对(
        self, tmp_path
    ) -> None:
        """窗口内直注行合成 tool_start/tool_end 对; 窗口外与无关行忽略."""
        from scripts.conversation_test_lib.analyzers import _parse_epoch
        from scripts.conversation_test_lib.collectors import (
            collect_direct_inject_events,
        )

        log_file = tmp_path / "server_8011.log"
        log_file.write_text(
            "\n".join([
                "2026-09-15 21:40:00,000 - src.tools.middleware._image_inject"
                " - INFO - 🖼️ 知识库图片直注主对话: tea:旧会话.jpg (100 bytes)",
                _INJECT_LINE,
                "2026-09-15 21:52:57,000 - src.agent - INFO - 无关行",
            ])
            + "\n",
            encoding="utf-8",
        )
        session_start = datetime(2026, 9, 15, 21, 50, 0)

        events = collect_direct_inject_events(session_start, tmp_path, [log_file])

        assert len(events) == 2
        start_ev, end_ev = events
        assert start_ev["type"] == "tool_start"
        assert start_ev["data"]["tool_name"] == "kb_read_image"
        assert "page0806_174.jpg" in start_ev["data"]["input_preview"]
        assert end_ev["type"] == "tool_end"
        assert end_ev["data"]["tool_name"] == "kb_read_image"
        assert end_ev["data"]["success"] is True
        # ts 须为可分桶的真 epoch (本地日志时间 → UTC iso 往返一致)
        expected_epoch = datetime(2026, 9, 15, 21, 52, 56).timestamp()
        assert _parse_epoch(start_ev["ts"]) == expected_epoch

    def test_collect_direct_inject_events_同内容多日志文件_不重复计数(
        self, tmp_path
    ) -> None:
        """logs_dir 的 server_*.log 与额外路径含相同直注行时, 事件只计一次."""
        from scripts.conversation_test_lib.collectors import (
            collect_direct_inject_events,
        )

        (tmp_path / "server_8011.log").write_text(_INJECT_LINE + "\n", encoding="utf-8")
        extra = tmp_path / "extra.log"
        extra.write_text(_INJECT_LINE + "\n", encoding="utf-8")
        session_start = datetime(2026, 9, 15, 21, 50, 0)

        events = collect_direct_inject_events(session_start, tmp_path, [extra])

        starts = [e for e in events if e["type"] == "tool_start"]
        assert len(starts) == 1

    def test_collect_tool_call_logs_服务日志直注行_并入工具事件(self, tmp_path) -> None:
        """采集工具事件时自动并入服务日志直注事件, 调用方无需单独合并."""
        from scripts.conversation_test_lib.collectors import collect_tool_call_logs

        # tracker 事件 (jsonl)
        tracker_line = json.dumps(
            {
                "ts": "2026-09-15T13:52:52.041705+00:00",
                "type": "tool_end",
                "data": {"tool_name": "tea_knowledge", "success": True},
            },
            ensure_ascii=False,
        )
        (tmp_path / "tool_calls_2026-09-15_13-52-52.json").write_text(
            tracker_line + "\n", encoding="utf-8"
        )
        (tmp_path / "server_8011.log").write_text(_INJECT_LINE + "\n", encoding="utf-8")
        session_start_dt = datetime(2026, 9, 15, 21, 50, 0)
        session_start = session_start_dt.timestamp() - 300

        events = collect_tool_call_logs(
            session_start_dt,
            session_start,
            tmp_path,
            [tmp_path / "server_8011.log"],
        )

        tool_names = {
            e["data"]["tool_name"]
            for e in events
            if e.get("type") in ("tool_start", "tool_end")
        }
        assert {"tea_knowledge", "kb_read_image"} <= tool_names
