"""migrate_todo_to_user_level 脚本单元测试.

验证: 旧库发现 / 行迁移与 id 重分配 / 源文件改名幂等 / dry-run 不落盘.
用户级库写入走真实 (测试模式 test_data), 旧库用 tmp_path 构造.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session

from scripts.migrate_todo_to_user_level import (
    discover_legacy_todo_dbs,
    migrate_todo_to_user_level,
)
from src.storage.dao.async_database_manager import close_all_db_managers
from src.storage.models.todo import TodoItem, TodoPriority, TodoStatus


def _create_legacy_db(db_path: Path, rows: list[dict]) -> None:
    """在指定路径构造旧三级隔离 todo.db."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    TodoItem.__table__.create(engine)
    with Session(engine) as session:
        for row in rows:
            session.add(TodoItem(**row))
        session.commit()
    engine.dispose()


def _legacy_row(title: str, user_id: str, thread_id: str) -> dict:
    return {
        "title": title,
        "user_id": user_id,
        "thread_id": thread_id,
        "priority": TodoPriority.MEDIUM,
        "status": TodoStatus.PENDING,
    }


async def _read_user_level_count(user_id: str) -> int:
    from src.storage.service import create_todo_service

    service = await create_todo_service(user_id, "any_thread", agent_id="any_agent")
    todos = await service.list_todos(user_id, limit=1000)
    return len(todos)


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    await close_all_db_managers()


class TestDiscoverLegacyTodoDbs:
    def test_discovers_three_level_paths(self, tmp_path: Path) -> None:
        db1 = tmp_path / "user1" / "thread1" / "agent1" / "database" / "todo.db"
        db2 = tmp_path / "user1" / "thread2" / "agentA" / "database" / "todo.db"
        _create_legacy_db(db1, [_legacy_row("a", "user1", "thread1")])
        _create_legacy_db(db2, [_legacy_row("b", "user1", "thread2")])

        results = discover_legacy_todo_dbs(tmp_path)

        assert len(results) == 2
        assert {user for _, user in results} == {"user1"}

    def test_ignores_migrated_files(self, tmp_path: Path) -> None:
        db = tmp_path / "u" / "t" / "a" / "database" / "todo.db.migrated"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"placeholder")

        assert discover_legacy_todo_dbs(tmp_path) == []

    def test_empty_base(self, tmp_path: Path) -> None:
        assert discover_legacy_todo_dbs(tmp_path / "nonexist") == []


@pytest.mark.asyncio
class TestMigrateTodoToUserLevel:
    async def test_migrates_rows_and_renames_source(self, tmp_path: Path) -> None:
        db1 = tmp_path / "m_user1" / "thread1" / "agent1" / "database" / "todo.db"
        db2 = tmp_path / "m_user1" / "thread2" / "agentA" / "database" / "todo.db"
        _create_legacy_db(
            db1,
            [
                _legacy_row("任务A", "m_user1", "thread1"),
                _legacy_row("任务B", "m_user1", "thread1"),
            ],
        )
        _create_legacy_db(db2, [_legacy_row("任务C", "m_user1", "thread2")])

        report = await migrate_todo_to_user_level(tmp_path)

        assert report.errors == []
        assert report.migrated_rows == 3
        assert len(report.migrated_files) == 2
        # 源文件已改名
        assert not db1.exists()
        assert db1.with_name("todo.db.migrated").exists()
        # 用户级库包含全部 3 条 (跨线程统一)
        assert await _read_user_level_count("m_user1") == 3

    async def test_idempotent_on_rerun(self, tmp_path: Path) -> None:
        db = tmp_path / "m_user2" / "t" / "a" / "database" / "todo.db"
        _create_legacy_db(db, [_legacy_row("任务", "m_user2", "t")])

        first = await migrate_todo_to_user_level(tmp_path)
        assert first.migrated_rows == 1

        second = await migrate_todo_to_user_level(tmp_path)
        assert second.migrated_rows == 0
        assert second.migrated_files == []
        assert await _read_user_level_count("m_user2") == 1

    async def test_dry_run_does_not_migrate(self, tmp_path: Path) -> None:
        db = tmp_path / "m_user3" / "t" / "a" / "database" / "todo.db"
        _create_legacy_db(db, [_legacy_row("任务", "m_user3", "t")])

        report = await migrate_todo_to_user_level(tmp_path, dry_run=True)

        assert report.migrated_rows == 0
        assert db.exists()  # 未改名
        assert len(report.skipped_files) == 1

    async def test_empty_shell_db_migrates_as_zero_rows(
        self, tmp_path: Path
    ) -> None:
        """空壳旧库 (建过连接但从未建表) 按 0 行迁移并改名, 不报错.

        生产实例: data/jxt/main/personal-assistant/database/todo.db
        为 4096 字节零对象库, 首轮迁移误报 "no such table" 记入 errors.
        """
        from sqlalchemy import text

        db = tmp_path / "m_shell" / "t" / "a" / "database" / "todo.db"
        db.parent.mkdir(parents=True)
        engine = create_engine(f"sqlite:///{db}")
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))  # 只物化空库, 不建表
        engine.dispose()

        report = await migrate_todo_to_user_level(tmp_path)

        assert report.errors == []
        assert report.migrated_rows == 0
        assert len(report.migrated_files) == 1
        assert not db.exists()
        assert db.with_name("todo.db.migrated").exists()
