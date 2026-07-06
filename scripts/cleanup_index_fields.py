"""清理旧数据库中已废弃的 title/keywords 列.

随 v1.8.x 记忆体系调整, conversation_index 表的 title 与 keywords 列从生成到存储
全面移除 (跨语言召回已由 BGE-M3 向量路覆盖, 这两个字段变为只写不读的死字段).
本脚本扫描所有 conversation_history.db, 用 ALTER TABLE ... DROP COLUMN 彻底删除
这两列, 使旧库 schema 与新 SQLModel 对齐.

要求: SQLite >= 3.35.0 (支持 DROP COLUMN). Python 3.12 自带版本均满足.

用法:
    python scripts/cleanup_index_fields.py --dry-run   # 预览: 列出将处理的库
    python scripts/cleanup_index_fields.py             # 执行清理 (先备份再删列)
    python scripts/cleanup_index_fields.py --data-dir /path/to/data

安全:
    - 每个 db 先写 .bak 备份 (幂等: 已存在同名 .bak 则跳过备份)
    - PRAGMA table_info 检查列存在再 DROP, 已清理的库重复运行无副作用
    - 单库 DROP 两列包在同一事务, 失败回滚
    - 自动跳过 chroma.sqlite3 等非对话库
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# 待删除的列 (顺序无关, 均不在任何索引/唯一约束中)
TARGET_COLUMNS = ("title", "keywords")
# 仅处理这个表名
TARGET_TABLE = "conversation_index"
# 只扫描这个文件名 (其余 .db 如 pinned_memory/todo 与本任务无关)
TARGET_DB_FILENAME = "conversation_history.db"
# DROP COLUMN 所需最低 SQLite 版本
MIN_SQLITE_VERSION = (3, 35, 0)


def check_sqlite_version() -> None:
    """校验运行环境 SQLite 版本支持 DROP COLUMN."""
    ver = sqlite3.sqlite_version_info
    if ver < MIN_SQLITE_VERSION:
        sys.exit(
            f"❌ SQLite 版本 {sqlite3.sqlite_version} 不支持 DROP COLUMN, "
            f"需要 >= {'.'.join(map(str, MIN_SQLITE_VERSION))}"
        )


def find_databases(data_dir: Path) -> list[Path]:
    """递归查找所有 conversation_history.db."""
    if not data_dir.exists():
        return []
    return sorted(data_dir.rglob(TARGET_DB_FILENAME))


def get_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """返回指定表的列名列表."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cursor.fetchall()]


def has_target_table(conn: sqlite3.Connection) -> bool:
    """判断库中是否存在 conversation_index 表."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (TARGET_TABLE,),
    )
    return cursor.fetchone() is not None


def backup_db(db_path: Path) -> bool:
    """备份数据库到 .bak 文件. 已存在则跳过 (幂等).

    Returns:
        True 表示本次新建了备份, False 表示已存在备份而跳过.
    """
    bak_path = db_path.with_suffix(db_path.suffix + ".bak")
    if bak_path.exists():
        return False
    shutil.copy2(db_path, bak_path)
    return True


def cleanup_one(db_path: Path, dry_run: bool) -> str:
    """清理单个数据库.

    Returns:
        状态字符串: cleaned / skipped / error
    """
    conn = sqlite3.connect(str(db_path))
    try:
        if not has_target_table(conn):
            return "skipped"

        columns = get_columns(conn, TARGET_TABLE)
        to_drop = [c for c in TARGET_COLUMNS if c in columns]
        if not to_drop:
            return "skipped"

        if dry_run:
            return "cleaned"

        # 备份 (正式模式下)
        backup_db(db_path)

        # 单事务删除目标列
        try:
            conn.execute("BEGIN")
            for col in to_drop:
                conn.execute(f"ALTER TABLE {TARGET_TABLE} DROP COLUMN {col}")
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise

        return "cleaned"
    finally:
        conn.close()


def main() -> None:
    """脚本入口."""
    parser = argparse.ArgumentParser(
        description="清理旧 conversation_history.db 中已废弃的 title/keywords 列",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=PROJECT_ROOT / "data",
        help=f"数据根目录 (默认: {PROJECT_ROOT / 'data'})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览, 不修改任何文件",
    )
    args = parser.parse_args()

    check_sqlite_version()

    databases = find_databases(args.data_dir)
    mode = "DRY-RUN (只预览)" if args.dry_run else "正式 (备份后删列)"
    print(f"模式: {mode}")
    print(f"扫描目录: {args.data_dir}")
    print(f"找到 {len(databases)} 个 conversation_history.db\n")
    print(f"{'数据库':<60} {'状态':<10} {'待删列'}")
    print("-" * 90)

    stats = {"cleaned": 0, "skipped": 0, "error": 0}
    for db_path in databases:
        rel = db_path.relative_to(args.data_dir) if db_path.is_relative_to(args.data_dir) else db_path
        try:
            # 预读待删列用于展示 (dry_run 与正式均展示)
            preview_conn = sqlite3.connect(str(db_path))
            try:
                if not has_target_table(preview_conn):
                    pending = []
                else:
                    cols = get_columns(preview_conn, TARGET_TABLE)
                    pending = [c for c in TARGET_COLUMNS if c in cols]
            finally:
                preview_conn.close()

            status = cleanup_one(db_path, args.dry_run)
            stats[status] += 1
            pending_str = ",".join(pending) if pending else "-"
            print(f"{rel!s:<60} {status:<10} {pending_str}")
        except Exception as e:
            stats["error"] += 1
            print(f"{rel!s:<60} {'error':<10} {e}")

    print("-" * 90)
    print(
        f"\n汇总 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"清理: {stats['cleaned']} | 跳过(已清理/无表): {stats['skipped']} | "
        f"错误: {stats['error']}"
    )
    if args.dry_run and stats["cleaned"] > 0:
        print("\n( dry-run 预览, 未做任何修改. 去掉 --dry-run 执行清理. )")


if __name__ == "__main__":
    main()
