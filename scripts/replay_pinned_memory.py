"""从对话历史重放置顶记忆提取.

按照1-step操作化方案, 从 conversation_history.db 逐轮重新提取置顶记忆.
每轮基于已积累的记忆做增量判断, 天然去重. 必须从空记忆开始按轮次顺序处理.

用法:
    python scripts/replay_pinned_memory.py --user gifford
    python scripts/replay_pinned_memory.py --user gifford --dry-run
    python scripts/replay_pinned_memory.py --user wy --agent personal-assistant
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("replay_pinned_memory")


def get_db_path(user_id: str, thread_id: str, agent_id: str, db_name: str) -> Path:
    """获取数据库文件路径."""
    return (
        PROJECT_ROOT
        / "data"
        / user_id
        / thread_id
        / agent_id
        / "database"
        / f"{db_name}.db"
    )


def load_conversations(
    user_id: str,
    thread_id: str,
    agent_id: str,
) -> list[dict]:
    """从 conversation_history.db 加载对话记录, 按轮次排序."""
    db_path = get_db_path(user_id, thread_id, agent_id, "conversation_history")
    if not db_path.exists():
        logger.error("对话数据库不存在: %s", db_path)
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT round_number, user_message, assistant_response, created_at "
        "FROM conversation_index ORDER BY round_number",
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def backup_pinned_db(pinned_db: Path) -> Path | None:
    """备份 pinned_memory.db 及其 WAL/SHM 文件."""
    if not pinned_db.exists():
        logger.warning("置顶记忆数据库不存在, 将新建: %s", pinned_db)
        return None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = pinned_db.parent / f"pinned_memory.db.bak.{ts}"
    shutil.copy2(str(pinned_db), str(backup))

    for suffix in ("-wal", "-shm"):
        sidecar = pinned_db.parent / f"pinned_memory.db{suffix}"
        if sidecar.exists():
            shutil.copy2(str(sidecar), str(backup.parent / f"{backup.name}{suffix}"))

    logger.info("已备份: %s", backup)
    return backup


def clear_pinned_memory(pinned_db: Path) -> None:
    """清空 simple_pinned_memory 表."""
    conn = sqlite3.connect(str(pinned_db))
    conn.execute("DELETE FROM simple_pinned_memory")
    conn.commit()
    conn.close()
    logger.info("已清空置顶记忆: %s", pinned_db)


def restore_pinned_db(pinned_db: Path, backup: Path) -> None:
    """从备份恢复 pinned_memory.db."""
    shutil.copy2(str(backup), str(pinned_db))
    for suffix in ("-wal", "-shm"):
        backup_sidecar = backup.parent / f"{backup.name}{suffix}"
        target_sidecar = pinned_db.parent / f"pinned_memory.db{suffix}"
        if backup_sidecar.exists():
            shutil.copy2(str(backup_sidecar), str(target_sidecar))
        elif target_sidecar.exists():
            target_sidecar.unlink()

    logger.info("已从备份恢复: %s", backup)


async def display_final_memory(
    user_id: str,
    thread_id: str,
    agent_id: str,
) -> str:
    """读取并返回最终记忆的格式化字符串."""
    from src.agent.memory.local_memory.pinned_memory import (
        SimplePinnedMemoryManager,
    )

    manager = SimplePinnedMemoryManager(user_id, thread_id, agent_id=agent_id)
    memory_block = await manager.get_memory_for_analysis()
    return memory_block


PRODUCTION_MODEL = "deepseek:deepseek-v4-flash"
PRODUCTION_MODEL_PARAMS: dict = {
    "max_tokens": 2048,
    "extra_body": {"thinking": {"type": "disabled"}},
}


async def replay(
    user_id: str,
    thread_id: str,
    agent_id: str,
    dry_run: bool = False,
) -> None:
    """执行重放提取."""
    conversations = load_conversations(user_id, thread_id, agent_id)
    if not conversations:
        logger.error("没有找到对话记录")
        return

    mode = "DRY-RUN (预览后恢复)" if dry_run else "正式 (覆盖写入)"
    logger.info("用户: %s, 对话: %d 轮, 模式: %s", user_id, len(conversations), mode)

    pinned_db = get_db_path(user_id, thread_id, agent_id, "pinned_memory")

    backup = backup_pinned_db(pinned_db)
    clear_pinned_memory(pinned_db)

    from src.agent.memory.local_memory.pinned_memory import (
        SimplePinnedMemoryManager,
    )
    from src.inference.content_analyzer.simple_analyzer import (
        SimpleContentAnalyzer,
    )

    manager = SimplePinnedMemoryManager(user_id, thread_id, agent_id=agent_id)
    analyzer = SimpleContentAnalyzer(
        config_override={
            "model_id": PRODUCTION_MODEL,
            "model_params": PRODUCTION_MODEL_PARAMS,
        },
    )
    logger.info(
        "提取模型: %s (与生产环境一致)",
        analyzer.model_id,
    )

    total_ops = 0
    rounds_with_ops = 0

    for conv in conversations:
        rn = conv["round_number"]
        user_msg = conv["user_message"]
        preview = user_msg[:50].replace("\n", " ").strip()

        try:
            memory_block = await manager.get_memory_for_analysis()
            result = await analyzer.analyze_pinned_memory_update(
                user_message=user_msg,
                todo_list="",
                memory_block=memory_block,
            )

            if result.has_operations and result.operations:
                updated = await manager.apply_operations(
                    result.operations,
                )
                if updated:
                    rounds_with_ops += 1
                    total_ops += len(result.operations)
                    for op in result.operations:
                        if op.action == "add":
                            content_preview = (op.content or "")[:60].replace(
                                "\n", " "
                            )
                            logger.info(
                                "  R%d +ADD [%s]: %s",
                                rn,
                                op.field,
                                content_preview,
                            )
                        elif op.action == "delete":
                            content_preview = (op.content or "")[:60].replace(
                                "\n", " "
                            )
                            logger.info(
                                "  R%d -DEL [%s]: %s",
                                rn,
                                op.field,
                                content_preview,
                            )
                        elif op.action == "change":
                            old_preview = (op.old_content or "")[:40].replace(
                                "\n", " "
                            )
                            new_preview = (op.new_content or "")[:40].replace(
                                "\n", " "
                            )
                            logger.info(
                                "  R%d ~CHG [%s]: %s -> %s",
                                rn,
                                op.field,
                                old_preview,
                                new_preview,
                            )

            logger.info("R%d done | %s...", rn, preview)
        except Exception as e:
            logger.error("R%d failed: %s", rn, e)

        await asyncio.sleep(1)

    logger.info("=" * 60)
    logger.info(
        "重放完成: %d 轮处理, %d 轮有操作, 共 %d 次操作",
        len(conversations),
        rounds_with_ops,
        total_ops,
    )

    final_memory = await display_final_memory(user_id, thread_id, agent_id)
    logger.info("最终置顶记忆:\n%s", final_memory)

    if dry_run and backup is not None:
        restore_pinned_db(pinned_db, backup)
        logger.info("DRY-RUN 完成, 已从备份恢复原始数据")


def main() -> None:
    parser = argparse.ArgumentParser(description="从对话历史重放置顶记忆提取")
    parser.add_argument("--user", required=True, help="用户ID")
    parser.add_argument(
        "--thread",
        default="main",
        help="线程ID (默认: main)",
    )
    parser.add_argument(
        "--agent",
        default="personal-assistant",
        help="Agent ID (默认: personal-assistant)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式: 提取后从备份恢复, 不保留结果",
    )
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    asyncio.run(
        replay(
            args.user,
            args.thread,
            args.agent,
            dry_run=args.dry_run,
        ),
    )


if __name__ == "__main__":
    main()
