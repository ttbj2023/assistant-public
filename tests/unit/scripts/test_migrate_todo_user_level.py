"""migrate_todo_to_user_level 脚本单元测试.

覆盖 --base-path 隔离完整性: 显式 base_path 时发现源与写入目标都必须限制
在该目录下, 不得泄漏到 path_resolver 生产路径 (2026-09-16 审计实测泄漏:
副本迁移把行写进了生产 bob 用户级库).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session

from scripts.migrate_todo_to_user_level import migrate_todo_to_user_level
from src.storage.models.todo import TodoItem, TodoPriority, TodoStatus


def _make_legacy_db(path: Path, titles: list[str]) -> None:
    """构造带 todo_items 表与数据的旧三级库 (SQLModel 建表保 schema 对齐)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")
    TodoItem.__table__.create(engine)
    with Session(engine) as session:
        for t in titles:
            session.add(
                TodoItem(
                    title=t,
                    user_id="u9",
                    thread_id="main",
                    priority=TodoPriority.MEDIUM,
                    status=TodoStatus.PENDING,
                )
            )
        session.commit()
    engine.dispose()


class TestBasePathIsolation:
    @pytest.mark.asyncio
    async def test_write_target_stays_under_base_path(self, tmp_path):
        """显式 base_path 时写入目标必须是 base_path/{user}/database/todo.db."""
        legacy = (
            tmp_path / "u9" / "main" / "personal-assistant" / "database" / "todo.db"
        )
        _make_legacy_db(legacy, ["任务A", "任务B"])

        report = await migrate_todo_to_user_level(tmp_path, dry_run=False)

        assert len(report.migrated_files) == 1
        assert report.migrated_rows == 2
        import sqlite3

        target = tmp_path / "u9" / "database" / "todo.db"
        assert target.exists(), "写入目标必须在 base_path 下"
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        rows = conn.execute("SELECT title FROM todo_items ORDER BY title").fetchall()
        conn.close()
        assert [r[0] for r in rows] == ["任务A", "任务B"]

    @pytest.mark.asyncio
    async def test_base_path_run_leaves_production_resolver_path_untouched(
        self, tmp_path, monkeypatch
    ):
        """base_path 迁移不得向 path_resolver 生产路径写入任何行."""
        from src.core import path_resolver as pr

        prod_base = pr.get_user_path_resolver().base_path
        prod_probe = prod_base / "u9-probe" / "database" / "todo.db"

        legacy = tmp_path / "u9-probe" / "t1" / "a1" / "database" / "todo.db"
        _make_legacy_db(legacy, ["隔离任务"])

        await migrate_todo_to_user_level(tmp_path, dry_run=False)

        assert not prod_probe.exists(), (
            "base_path 迁移把数据写进了 path_resolver 生产路径 — 隔离泄漏"
        )
