"""文件注册表数据迁移脚本.

将旧 Agent 级 attachment_registry + 用户级 file_hash_index 数据
迁移到统一的用户级 file_registry (Phase 6 核心切换).

迁移内容:
- attachment_registry 每条记录 → FileEntry (physical_path 从 file_hash_index 获取或推导)
- 图片类型的 detail (AI 画面描述) → 写入 .desc.md (文档 detail 原文不迁移, 符合"不要原文"决策)

迁移后:
- 物理文件位置不变 (仍在 {thread}/{agent}/shared/files/)
- 旧表保留 (Phase 7 删除), 新代码只读写 file_registry

用法:
    python scripts/migrate_file_registry.py          # 迁移所有用户
    python scripts/migrate_file_registry.py --user U1  # 迁移指定用户
    python scripts/migrate_file_registry.py --dry-run  # 预览不写入
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
from pathlib import Path

from src.core.path_resolver import get_user_path_resolver
from src.files.desc_writer import desc_relative_path, write_desc
from src.storage.models.file_registry import FileEntry
from src.storage.service.file_registry_service import create_file_registry_service

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _load_hash_index(hash_db: Path) -> dict[str, tuple[str, int]]:
    """读取 file_hash_index, 返回 {content_hash: (physical_path, file_size)}."""
    if not hash_db.exists():
        return {}
    hash_map: dict[str, tuple[str, int]] = {}
    try:
        conn = sqlite3.connect(hash_db)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            "SELECT content_hash, physical_path, file_size FROM file_hash_index",
        ):
            hash_map[row["content_hash"]] = (
                row["physical_path"],
                row["file_size"],
            )
        conn.close()
    except sqlite3.OperationalError as e:
        logger.warning("读取 file_hash_index 失败 %s: %s", hash_db, e)
    return hash_map


def _derive_thread_agent(user_dir: Path, db_path: Path) -> tuple[str, str]:
    """从 db_path 相对路径推导 thread_id / agent_id.

    db_path 结构: {user_dir}/{thread}/{agent}/database/conversation_history.db
    """
    rel = db_path.relative_to(user_dir)
    parts = rel.parts
    thread_id = parts[0] if len(parts) > 0 else "unknown"
    agent_id = parts[1] if len(parts) > 1 else "unknown"
    return thread_id, agent_id


async def migrate_user(user_id: str, dry_run: bool = False) -> tuple[int, int]:
    """迁移单个用户, 返回 (迁移记录数, 写入 .desc.md 数)."""
    resolver = get_user_path_resolver()
    try:
        user_dir = resolver.get_user_base_path(user_id)
    except ValueError:
        logger.debug("跳过 (非法用户ID): %s", user_id)
        return 0, 0

    if not user_dir.exists():
        logger.info("跳过 (目录不存在): %s", user_id)
        return 0, 0

    hash_map = _load_hash_index(user_dir / "database" / "file_store.db")
    logger.info("📂 用户 %s: 加载 %d 条哈希索引", user_id, len(hash_map))

    registry = None if dry_run else await create_file_registry_service(user_id)

    migrated = 0
    desc_written = 0

    for db_path in user_dir.rglob("conversation_history.db"):
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='attachment_registry'",
            ).fetchall()
            if not tables:
                continue

            thread_id, agent_id = _derive_thread_agent(user_dir, db_path)
            rows = conn.execute(
                "SELECT file_id, file_type, internal_path, filename, brief, "
                "detail, file_format, file_size, content_hash, round_number, "
                "document_meta FROM attachment_registry",
            ).fetchall()

            for row in rows:
                content_hash = row["content_hash"]
                if content_hash and content_hash in hash_map:
                    physical_path, hash_size = hash_map[content_hash]
                    file_size = hash_size or row["file_size"] or 0
                else:
                    physical_path = f"{thread_id}/shared/{row['internal_path']}"
                    file_size = row["file_size"] or 0

                entry = FileEntry(
                    file_id=row["file_id"],
                    file_type=row["file_type"],
                    physical_path=physical_path,
                    desc_path=desc_relative_path(row["file_id"]),
                    filename=row["filename"],
                    brief=row["brief"] or "",
                    file_format=row["file_format"],
                    file_size=file_size,
                    content_hash=content_hash,
                    round_number=row["round_number"] or 0,
                    owner_thread_id=thread_id,
                    owner_agent_id=agent_id,
                    document_meta=row["document_meta"],
                )

                if registry:
                    await registry.upsert(entry)
                migrated += 1

                # 图片的 detail (画面描述) 迁移到 .desc.md; 文档原文不迁移
                if row["file_type"] == "image" and row["detail"]:
                    if not dry_run:
                        write_desc(user_id, row["file_id"], row["detail"])
                    desc_written += 1

        except sqlite3.OperationalError as e:
            logger.warning("读取 attachment_registry 失败 %s: %s", db_path, e)
        finally:
            if conn:
                conn.close()

    logger.info(
        "✅ 用户 %s: 迁移 %d 条记录, 写入 %d 个 .desc.md%s",
        user_id,
        migrated,
        desc_written,
        " (dry-run)" if dry_run else "",
    )
    return migrated, desc_written


async def main() -> None:
    parser = argparse.ArgumentParser(description="文件注册表数据迁移")
    parser.add_argument("--user", help="只迁移指定用户")
    parser.add_argument("--dry-run", action="store_true", help="预览不写入")
    args = parser.parse_args()

    resolver = get_user_path_resolver()
    data_root = resolver.base_path

    if args.user:
        user_ids = [args.user]
    else:
        user_ids = [
            d.name for d in data_root.iterdir() if d.is_dir() and d.name != "test_data"
        ]

    logger.info("🚀 开始迁移 %d 个用户%s", len(user_ids), " (dry-run)" if args.dry_run else "")

    total_migrated = 0
    total_desc = 0
    for user_id in user_ids:
        m, d = await migrate_user(user_id, dry_run=args.dry_run)
        total_migrated += m
        total_desc += d

    logger.info(
        "\n📊 迁移完成: %d 条记录, %d 个 .desc.md%s",
        total_migrated,
        total_desc,
        " (dry-run)" if args.dry_run else "",
    )


if __name__ == "__main__":
    asyncio.run(main())
