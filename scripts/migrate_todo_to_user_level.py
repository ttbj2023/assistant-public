#!/usr/bin/env python3
"""TODO 数据迁移: 三级隔离库 → 用户级统一库.

背景: todo 存储从 data/{user}/{thread}/{agent}/database/todo.db 迁移为
data/{user_id}/database/todo.db (跨线程统一视图).

行为:
- 扫描 base_path 下所有旧三级路径的 todo.db
- 按用户合并行到用户级库 (id 重新分配, 其余字段保留)
- 源文件改名 todo.db.migrated (天然备份 + 幂等: 再次运行自动跳过)
- --dry-run 只输出计划不执行

用法:
    python scripts/migrate_todo_to_user_level.py [--dry-run] [--base-path PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_todo")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.dao.async_database_manager import (  # noqa: E402
    AsyncDatabaseManager,
    close_all_db_managers,
    create_async_todo_db_manager,
)
from src.storage.dao.database_operations import AsyncDatabaseOperations  # noqa: E402
from src.storage.models.todo import TodoItem  # noqa: E402


@dataclass
class MigrationReport:
    """迁移结果报告."""

    scanned_users: int = 0
    migrated_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    migrated_rows: int = 0
    errors: list[str] = field(default_factory=list)


def discover_legacy_todo_dbs(base_path: Path) -> list[tuple[Path, str]]:
    """扫描旧三级隔离路径的 todo.db.

    Returns:
        (db_path, user_id) 列表

    """
    results: list[tuple[Path, str]] = []
    if not base_path.exists():
        return results
    for user_dir in sorted(base_path.iterdir()):
        if not user_dir.is_dir():
            continue
        for thread_dir in user_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            for agent_dir in thread_dir.iterdir():
                if not agent_dir.is_dir():
                    continue
                db_path = agent_dir / "database" / "todo.db"
                if db_path.exists():
                    results.append((db_path, user_dir.name))
    return results


async def _read_legacy_rows(db_path: Path) -> list[TodoItem]:
    """读取旧库全部 TODO 行.

    空壳库 (建过连接但从未建表) 视为 0 行, 不作错误 — 生产实测存在
    data/jxt/main/personal-assistant/database/todo.db 为 4096 字节零对象库.
    """
    from sqlalchemy.exc import OperationalError

    manager = AsyncDatabaseManager(f"sqlite+aiosqlite:///{db_path}")
    db_ops = AsyncDatabaseOperations(manager.session_factory, TodoItem)
    try:
        return await db_ops.list_all(limit=100000)
    except OperationalError as e:
        if "no such table" not in str(e):
            raise
        logger.info("旧库无 todo_items 表 (空壳), 按 0 行处理: %s", db_path)
        return []
    finally:
        await manager.close()


async def _write_user_level_rows(user_id: str, rows: list[TodoItem]) -> int:
    """把行写入用户级库 (id 重新分配)."""
    db_manager = await create_async_todo_db_manager(user_id)
    written = 0
    async with db_manager.session_factory() as session:
        for row in rows:
            data = row.model_dump(exclude={"id"}, exclude_unset=False)
            data.pop("id", None)
            session.add(TodoItem(**data))
            written += 1
        await session.commit()
    return written


async def migrate_todo_to_user_level(
    base_path: Path | None = None,
    *,
    dry_run: bool = False,
) -> MigrationReport:
    """执行迁移.

    Args:
        base_path: 数据根目录 (默认取 path_resolver 的 base_path)
        dry_run: 只输出计划不执行

    Returns:
        迁移报告

    """
    if base_path is None:
        from src.core.path_resolver import get_user_path_resolver

        base_path = get_user_path_resolver().base_path

    report = MigrationReport()
    legacy_dbs = discover_legacy_todo_dbs(base_path)
    users = {user_id for _, user_id in legacy_dbs}
    report.scanned_users = len(users)

    if not legacy_dbs:
        logger.info("未发现旧三级隔离的 todo.db, 无需迁移")
        return report

    for db_path, user_id in legacy_dbs:
        logger.info(
            "发现旧库: %s (user=%s)%s",
            db_path,
            user_id,
            " [dry-run]" if dry_run else "",
        )
        if dry_run:
            report.skipped_files.append(str(db_path))
            continue
        try:
            rows = await _read_legacy_rows(db_path)
            written = await _write_user_level_rows(user_id, rows)
            report.migrated_rows += written
            report.migrated_files.append(str(db_path))
            db_path.rename(db_path.with_name(db_path.name + ".migrated"))
            logger.info("✅ 迁移完成: %s (%d 行)", db_path, written)
        except Exception as e:
            report.errors.append(f"{db_path}: {e}")
            logger.error("❌ 迁移失败: %s, error: %s", db_path, e)

    await close_all_db_managers()
    return report


def main() -> int:
    """命令行入口."""
    parser = argparse.ArgumentParser(description="TODO 三级隔离库迁移到用户级")
    parser.add_argument("--dry-run", action="store_true", help="只输出计划不执行")
    parser.add_argument("--base-path", type=Path, default=None, help="数据根目录覆盖")
    args = parser.parse_args()

    report = asyncio.run(
        migrate_todo_to_user_level(args.base_path, dry_run=args.dry_run),
    )
    print(
        f"\n迁移报告: 用户 {report.scanned_users} 个, "
        f"文件迁移 {len(report.migrated_files)} 个, "
        f"行迁移 {report.migrated_rows} 条, "
        f"错误 {len(report.errors)} 个",
    )
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
