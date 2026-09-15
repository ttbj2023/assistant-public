"""conversation_test 采集器 (collectors) 的单元测试.

验证 TODO 用户级数据库迁移后, collect_db_data 从用户级目录采集 todo 记录
(而非线程级 agent 目录, 迁移前的旧位置).
"""

from __future__ import annotations

import sqlite3
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
