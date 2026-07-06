"""从对话历史重建对话索引摘要 (topic/summary).

对 conversation_history.db 中索引字段为空 (或需刷新) 的轮次, 逐轮调用内容分析器
重新生成索引摘要, 通过 ConversationService.create_conversation 的 UPSERT 覆盖回填.
不修改 user_message/assistant_response/created_at, 仅更新索引字段.

用法:
    python scripts/rebuild_conversation_index.py --user alice
    python scripts/rebuild_conversation_index.py --user alice --dry-run
    python scripts/rebuild_conversation_index.py --user alice --start 1 --end 30
    python scripts/rebuild_conversation_index.py --user wy --agent personal-assistant
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("rebuild_conversation_index")


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
    start: int | None = None,
    end: int | None = None,
    only_empty: bool = False,
) -> list[dict]:
    """从 conversation_history.db 加载对话记录, 按轮次排序.

    Args:
        only_empty: 仅加载索引字段为空的轮次 (summary 为空视为缺索引)

    """
    db_path = get_db_path(user_id, thread_id, agent_id, "conversation_history")
    if not db_path.exists():
        logger.error("对话数据库不存在: %s", db_path)
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = (
        "SELECT round_number, user_message, assistant_response, "
        "topic, summary, created_at "
        "FROM conversation_index"
    )
    conditions: list[str] = []
    params: list[object] = []

    if start is not None:
        conditions.append("round_number >= ?")
        params.append(start)
    if end is not None:
        conditions.append("round_number <= ?")
        params.append(end)
    if only_empty:
        conditions.append("(summary IS NULL OR summary = '')")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY round_number"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


PRODUCTION_MODEL = "deepseek:deepseek-v4-flash"
PRODUCTION_MODEL_PARAMS: dict = {
    "max_tokens": 2048,
    "extra_body": {"thinking": {"type": "disabled"}},
}


async def rebuild(
    user_id: str,
    thread_id: str,
    agent_id: str,
    dry_run: bool = False,
    start: int | None = None,
    end: int | None = None,
    only_empty: bool = False,
) -> None:
    """执行索引重建."""
    conversations = load_conversations(
        user_id, thread_id, agent_id, start=start, end=end, only_empty=only_empty
    )
    if not conversations:
        logger.error("没有找到需要重建的对话记录")
        return

    mode = "DRY-RUN (只预览不写入)" if dry_run else "正式 (覆盖写入)"
    logger.info(
        "用户: %s, 对话: %d 轮, 模式: %s",
        user_id,
        len(conversations),
        mode,
    )

    from src.inference.content_analyzer.simple_analyzer import (
        SimpleContentAnalyzer,
    )
    from src.storage.service.service_factory import create_conversation_service

    analyzer = SimpleContentAnalyzer(
        config_override={
            "model_id": PRODUCTION_MODEL,
            "model_params": PRODUCTION_MODEL_PARAMS,
        },
    )
    logger.info("索引生成模型: %s (与生产环境一致)", analyzer.model_id)

    if not dry_run:
        conv_service = await create_conversation_service(
            user_id, thread_id, agent_id=agent_id
        )

    success_count = 0
    fail_count = 0

    for conv in conversations:
        rn = conv["round_number"]
        user_msg = conv["user_message"]
        asst_msg = conv["assistant_response"]
        preview = user_msg[:50].replace("\n", " ").strip()

        try:
            result = await analyzer.analyze_conversation_index(
                user_message=user_msg,
                assistant_response=asst_msg,
            )

            metadata = {
                "topic": result.topic or "对话",
                "summary": result.summary or user_msg[:50],
            }

            if dry_run:
                logger.info(
                    "  R%d [预览] topic=%s | summary=%s",
                    rn,
                    metadata["topic"],
                    metadata["summary"][:40],
                )
            else:
                await conv_service.create_conversation(
                    user_message=user_msg,
                    assistant_response=asst_msg,
                    user_id=user_id,
                    thread_id=thread_id,
                    agent_id=agent_id,
                    metadata=metadata,
                    round_number=rn,
                )
                logger.info(
                    "  R%d done | %s | topic=%s",
                    rn,
                    preview,
                    metadata["topic"],
                )

            success_count += 1
        except Exception as e:
            fail_count += 1
            logger.error("R%d failed: %s", rn, e)

        await asyncio.sleep(1)

    logger.info("=" * 60)
    logger.info(
        "索引重建完成: %d 轮处理, 成功=%d, 失败=%d",
        len(conversations),
        success_count,
        fail_count,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="从对话历史重建对话索引摘要")
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
        help="预览模式: 只生成不写入",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=None,
        help="起始轮次 (默认: 1)",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=None,
        help="结束轮次 (默认: 最后一轮)",
    )
    parser.add_argument(
        "--only-empty",
        action="store_true",
        help="仅处理索引字段为空的轮次",
    )
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    asyncio.run(
        rebuild(
            args.user,
            args.thread,
            args.agent,
            dry_run=args.dry_run,
            start=args.start,
            end=args.end,
            only_empty=args.only_empty,
        )
    )


if __name__ == "__main__":
    main()
