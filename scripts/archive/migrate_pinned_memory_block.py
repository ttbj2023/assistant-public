"""迁移旧置顶记忆到统一单一块 (pinned_memory_block).

合并来源:
  - simple_pinned_memory 表 (basic_info / preferences / insights, 按 priority 降序)
  - requirement_memory.db 的 user_requirement 表 (content, 仅 personal-assistant)

输出: pinned_memory.db 内的 pinned_memory_block 表 (单行单字段 content).

用法:
  python scripts/migrate_pinned_memory_block.py --dry-run     # 预览
  python scripts/migrate_pinned_memory_block.py               # 执行迁移
  python scripts/migrate_pinned_memory_block.py --cleanup      # 迁移后清空旧表

流程: 备份 (.bak) → 读取旧表 → 合并 → 写入新表 → (可选)清理旧表.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# DB 存储 enum name (大写), 非 value; 顺序决定合并后的排列
MEMORY_TYPE_ORDER = ["BASIC_INFO", "PREFERENCES", "INSIGHTS"]


def find_pinned_databases(data_dir: Path) -> list[Path]:
    """扫描 data/ 目录树, 返回所有 pinned_memory.db 路径."""
    return sorted(data_dir.rglob("pinned_memory.db"))


def backup_database(db_path: Path) -> Path:
    """备份数据库文件到 .bak."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = db_path.with_suffix(f".db.bak_{timestamp}")
    shutil.copy2(db_path, bak_path)
    return bak_path


def read_simple_pinned_memory(db_path: Path) -> list[tuple[str, int, str]]:
    """读取 simple_pinned_memory 表, 返回 (memory_type, priority, content) 列表.

    表不存在或为空时返回空列表.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='simple_pinned_memory'"
        )
        if cursor.fetchone() is None:
            return []
        rows = conn.execute(
            "SELECT memory_type, priority, content FROM simple_pinned_memory "
            "WHERE content IS NOT NULL AND content != '' "
            "ORDER BY memory_type, priority DESC"
        ).fetchall()
        return [(r[0], r[1] or 50, r[2] or "") for r in rows]
    finally:
        conn.close()


def read_user_requirement(db_dir: Path) -> str:
    """读取同目录下 requirement_memory.db 的 user_requirement 表 content.

    不存在时返回空串.
    """
    req_db = db_dir / "requirement_memory.db"
    if not req_db.exists():
        return ""
    conn = sqlite3.connect(str(req_db))
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='user_requirement'"
        )
        if cursor.fetchone() is None:
            return ""
        row = conn.execute("SELECT content FROM user_requirement LIMIT 1").fetchone()
        return (row[0] if row else "") or ""
    finally:
        conn.close()


def merge_content(
    pinned_rows: list[tuple[str, int, str]],
    requirement_content: str,
) -> str:
    """合并旧数据为单一文本块 (一行一条).

    拼接顺序: basic_info → preferences → insights → requirement.
    每条记忆的 content 内部换行替换为空格, 保持"一行一条"语义.
    """
    lines: list[str] = []
    pinned_by_type: dict[str, list[str]] = {}
    for memory_type, _priority, content in pinned_rows:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped:
                pinned_by_type.setdefault(memory_type, []).append(stripped)

    for mt in MEMORY_TYPE_ORDER:
        lines.extend(pinned_by_type.get(mt, []))

    if requirement_content.strip():
        for line in requirement_content.splitlines():
            stripped = line.strip()
            if stripped:
                lines.append(stripped)

    return "\n".join(lines)


def ensure_block_table(conn: sqlite3.Connection) -> None:
    """确保 pinned_memory_block 表存在."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pinned_memory_block ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  user_id TEXT NOT NULL,"
        "  thread_id TEXT NOT NULL,"
        "  content TEXT DEFAULT '',"
        "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
        "  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
        "  UNIQUE(user_id, thread_id)"
        ")"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_thread "
        "ON pinned_memory_block(user_id, thread_id)"
    )


def extract_user_thread(db_path: Path) -> tuple[str, str]:
    """从 simple_pinned_memory 表推断 user_id / thread_id (取首行)."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT user_id, thread_id FROM simple_pinned_memory LIMIT 1"
        ).fetchone()
        if row:
            return row[0], row[1]
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
    return "", ""


def write_block(
    db_path: Path,
    user_id: str,
    thread_id: str,
    content: str,
) -> None:
    """写入 pinned_memory_block 表 (upsert)."""
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_block_table(conn)
        conn.execute(
            "INSERT INTO pinned_memory_block (user_id, thread_id, content, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, thread_id) DO UPDATE SET "
            "  content=excluded.content, updated_at=CURRENT_TIMESTAMP",
            (user_id, thread_id, content),
        )
        conn.commit()
    finally:
        conn.close()


def cleanup_old_tables(db_path: Path, db_dir: Path) -> None:
    """清空 simple_pinned_memory 表数据 + 删除 requirement_memory.db."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DELETE FROM simple_pinned_memory")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()

    req_db = db_dir / "requirement_memory.db"
    if req_db.exists():
        req_db.unlink()


def migrate_one(db_path: Path, *, dry_run: bool, cleanup: bool) -> bool:
    """迁移单个 pinned_memory.db. 返回是否有实际数据被迁移."""
    db_dir = db_path.parent

    pinned_rows = read_simple_pinned_memory(db_path)
    requirement_content = read_user_requirement(db_dir)

    if not pinned_rows and not requirement_content.strip():
        return False

    merged = merge_content(pinned_rows, requirement_content)
    user_id, thread_id = extract_user_thread(db_path)

    rel_path = (
        db_path.relative_to(DATA_DIR) if db_path.is_relative_to(DATA_DIR) else db_path
    )

    if dry_run:
        line_count = len([line for line in merged.splitlines() if line.strip()])
        print(f"[DRY-RUN] {rel_path}")
        print(
            f"  来源: {len(pinned_rows)} 条 pinned + "
            f"{'有' if requirement_content.strip() else '无'} requirement"
        )
        print(f"  合并: {line_count} 行, {len(merged)} 字")
        print(f"  预览:\n{_indent(merged)}")
        return True

    bak_path = backup_database(db_path)
    print(f"[BACKUP] {bak_path.name}")

    if user_id and thread_id:
        write_block(db_path, user_id, thread_id, merged)
    else:
        print("  [SKIP] 无法推断 user_id/thread_id, 跳过写入")
        return False

    line_count = len([line for line in merged.splitlines() if line.strip()])
    print(f"[MIGRATED] {rel_path}: {line_count} 行, {len(merged)} 字")

    if cleanup:
        cleanup_old_tables(db_path, db_dir)
        print("[CLEANED] 已清空 simple_pinned_memory + 删除 requirement_memory.db")

    return True


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="迁移旧置顶记忆到统一单一块 (pinned_memory_block)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式, 不写入",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="迁移后清空旧表 (simple_pinned_memory) 并删除 requirement_memory.db",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DATA_DIR,
        help=f"data 目录 (默认: {DATA_DIR})",
    )
    args = parser.parse_args()

    data_dir: Path = args.data_dir
    if not data_dir.exists():
        print(f"错误: data 目录不存在: {data_dir}", file=sys.stderr)
        sys.exit(1)

    databases = find_pinned_databases(data_dir)
    if not databases:
        print(f"未找到任何 pinned_memory.db (扫描: {data_dir})")
        return

    print(f"发现 {len(databases)} 个 pinned_memory.db\n")

    migrated = 0
    skipped = 0
    for db_path in databases:
        if migrate_one(db_path, dry_run=args.dry_run, cleanup=args.cleanup):
            migrated += 1
        else:
            skipped += 1
        print()

    print(f"完成: {migrated} 个已迁移, {skipped} 个无数据跳过")


if __name__ == "__main__":
    main()
