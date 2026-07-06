#!/usr/bin/env python3
"""从SQL对话历史重建向量数据库.

用法:
    python scripts/rebuild_vector_store.py [--user-id alice] [--thread-id main] [--agent-id health-assistant]

默认重建 alice/main 的 health-assistant 对话记录.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sqlite3
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.core.path_resolver import get_database_path, get_vector_path
from src.storage.langchain_vector_store import create_langchain_vector_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_conversation_records(db_path: str, agent_id: str) -> list[dict]:
    """从SQL数据库读取对话记录."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT round_number, agent_id, user_message, assistant_response "
        "FROM conversation_index WHERE agent_id = ? ORDER BY round_number",
        (agent_id,),
    )

    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


async def rebuild(
    user_id: str, thread_id: str, agent_id: str
) -> None:
    """重建向量数据库."""
    db_path = get_database_path(user_id, thread_id, "conversation_history", agent_id=agent_id)
    vector_path = get_vector_path(user_id, thread_id, agent_id=agent_id)

    if not Path(db_path).exists():
        logger.error("数据库文件不存在: %s", db_path)
        sys.exit(1)

    records = get_conversation_records(db_path, agent_id)
    total = len(records)
    logger.info("从 %s 读取到 %d 条记录", Path(db_path).name, total)

    if total == 0:
        logger.warning("没有需要重建的记录")
        return

    if vector_path.exists():
        logger.info("删除旧向量数据库: %s", vector_path)
        shutil.rmtree(vector_path)

    logger.info("创建向量存储实例 (user=%s, thread=%s, agent=%s)...", user_id, thread_id, agent_id)
    vector_store = create_langchain_vector_store(user_id, thread_id, agent_id=agent_id)

    # 4. 逐条写入
    success_count = 0
    fail_count = 0
    start_time = time.time()

    for i, record in enumerate(records, 1):
        rn = record["round_number"]
        aid = record["agent_id"]
        user_msg = record["user_message"] or ""
        asst_msg = record["assistant_response"] or ""

        if not user_msg.strip() or not asst_msg.strip():
            logger.warning("  [%d/%d] Round %d 跳过: 内容为空", i, total, rn)
            fail_count += 1
            continue

        try:
            await vector_store.add_conversation_round(
                round_number=rn,
                user_message=user_msg,
                assistant_response=asst_msg,
                agent_id=aid,
            )
            success_count += 1
            if i % 10 == 0 or i == total:
                elapsed = time.time() - start_time
                logger.info(
                    "  [%d/%d] 已处理, 成功=%d, 失败=%d, 耗时=%.1fs",
                    i, total, success_count, fail_count, elapsed,
                )
        except Exception as e:
            fail_count += 1
            logger.error("  [%d/%d] Round %d (agent=%s) 写入失败: %s", i, total, rn, aid, e)

    elapsed = time.time() - start_time
    logger.info(
        "重建完成: 成功=%d, 失败=%d, 总计=%d, 耗时=%.1fs",
        success_count, fail_count, total, elapsed,
    )

    # 5. 验证
    stats = vector_store.get_collection_stats()
    logger.info("向量存储统计: %s", stats)


def main() -> None:
    parser = argparse.ArgumentParser(description="从SQL对话历史重建向量数据库")
    parser.add_argument("--user-id", default="alice", help="用户ID (默认: alice)")
    parser.add_argument("--thread-id", default="main", help="线程ID (默认: main)")
    parser.add_argument("--agent-id", default="health-assistant", help="Agent ID (默认: health-assistant)")
    args = parser.parse_args()

    asyncio.run(rebuild(args.user_id, args.thread_id, args.agent_id))


if __name__ == "__main__":
    main()
