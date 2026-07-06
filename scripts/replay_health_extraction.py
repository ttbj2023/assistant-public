"""模拟健康数据后台提取 - 从已有对话历史中重放提取.

读取健康助手的对话历史, 逐条调用 UnifiedHealthExtractor 提取健康数据,
并通过 HealthDataExtractionService 存储到 health_data.db.

用法:
    python scripts/replay_health_extraction.py
    python scripts/replay_health_extraction.py --dry-run  # 仅提取不入库
    python scripts/replay_health_extraction.py --rounds 5,6,8,10  # 指定轮次
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("replay_health_extraction")

USER_ID = "alice"
THREAD_ID = "main"
AGENT_ID = "health-assistant"

CONVERSATION_DB = (
    PROJECT_ROOT
    / "data"
    / USER_ID
    / THREAD_ID
    / AGENT_ID
    / "database"
    / "conversation_history.db"
)


def load_conversations(rounds: list[int] | None = None) -> list[dict]:
    """从 conversation_history.db 加载对话记录."""
    if not CONVERSATION_DB.exists():
        logger.error(f"对话数据库不存在: {CONVERSATION_DB}")
        return []

    conn = sqlite3.connect(str(CONVERSATION_DB))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if rounds:
        placeholders = ",".join("?" * len(rounds))
        cursor.execute(
            f"SELECT id, round_number, user_message, created_at "
            f"FROM conversation_index "
            f"WHERE round_number IN ({placeholders}) "
            f"ORDER BY round_number",
            rounds,
        )
    else:
        cursor.execute(
            "SELECT id, round_number, user_message, created_at "
            "FROM conversation_index "
            "ORDER BY round_number"
        )

    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def parse_message_date(user_message: str) -> str:
    """从用户消息的时间前缀中提取日期.

    格式: [2026-05-26 15:45:55 CST] ...
    """
    match = re.match(r"\[(\d{4}-\d{2}-\d{2})", user_message)
    if match:
        return match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def build_combined_text(user_message: str) -> str:
    """构建提取器输入文本.

    将用户消息中的图片描述部分提取出来, 按后台提取器的格式组装.
    """
    parts = [f"用户消息:\n{user_message}"]

    # 检查消息中是否包含图片描述标记 [img: ...]
    img_pattern = re.compile(r"\[img:\s*([^\]]+?)\s*-\s*(.+?)(?:\])", re.DOTALL)
    img_matches = img_pattern.findall(user_message)

    if img_matches:
        image_descriptions = []
        for i, (filename, description) in enumerate(img_matches, 1):
            # 清理描述中的换行和多余空白
            clean_desc = " ".join(description.split())
            image_descriptions.append(f"[图片{i}描述]: {clean_desc}")
        parts.append("图片内容:\n" + "\n".join(image_descriptions))

    return "\n\n".join(parts)


async def replay_single(
    conversation: dict,
    extractor,
    service,
    dry_run: bool = False,
) -> list[dict]:
    """重放单条对话的提取."""
    round_number = conversation["round_number"]
    user_message = conversation["user_message"]
    current_date = parse_message_date(user_message)
    combined_text = build_combined_text(user_message)

    logger.info(f"=== Round {round_number} (date={current_date}) ===")
    logger.info(f"用户消息前100字: {user_message[:100]}...")

    # 检查是否包含图片描述
    has_images = "[img:" in user_message
    if has_images:
        logger.info("  包含图片描述")

    try:
        results = await extractor.extract(
            combined_text,
            current_date=current_date,
        )
    except Exception as e:
        logger.error(f"  提取失败: {e}")
        return []

    if not results:
        logger.info("  无健康数据")
        return []

    extracted = []
    for result in results:
        logger.info(f"  提取到: {result.data_type}")
        logger.info(f"  数据: {json.dumps(result.data, ensure_ascii=False, indent=2)[:500]}")

        if not dry_run:
            try:
                store_result = await service.store_extraction(
                    data_type=result.data_type,
                    data=result.data,
                    round_number=round_number,
                )
                if store_result.get("success"):
                    logger.info(f"  ✅ 存储成功: {store_result.get('message')}")
                else:
                    logger.warning(f"  ❌ 存储失败: {store_result.get('error')}")
            except Exception as e:
                logger.warning(f"  ❌ 存储异常: {e}")
        else:
            logger.info("  [DRY-RUN] 不入库")

        extracted.append({"round": round_number, "type": result.data_type, "data": result.data})

    return extracted


async def main():
    parser = argparse.ArgumentParser(description="从对话历史重放健康数据提取")
    parser.add_argument("--dry-run", action="store_true", help="仅提取不入库")
    parser.add_argument("--rounds", type=str, help="指定轮次, 逗号分隔 (如: 5,6,8,10)")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # 解析指定轮次
    rounds = None
    if args.rounds:
        rounds = [int(r.strip()) for r in args.rounds.split(",")]
        logger.info(f"指定轮次: {rounds}")

    # 加载对话
    conversations = load_conversations(rounds)
    if not conversations:
        logger.error("没有找到对话记录")
        return

    logger.info(f"加载了 {len(conversations)} 条对话记录")
    if args.dry_run:
        logger.info("** DRY-RUN 模式: 仅提取不入库 **")

    # 初始化提取器
    from src.inference.health_data_extraction.unified_extractor import (
        UnifiedHealthExtractor,
    )

    extractor = UnifiedHealthExtractor()
    if not extractor.is_available():
        logger.error("健康数据提取器不可用, 请检查 API Key 配置")
        return

    logger.info(f"提取器就绪: model={extractor.model_id}")

    # 初始化存储服务
    from src.storage.service.health_data_extraction_service import (
        get_health_data_extraction_service,
    )

    service = get_health_data_extraction_service(
        USER_ID, THREAD_ID, agent_id=AGENT_ID
    )

    # 逐条重放
    all_extracted = []
    for conv in conversations:
        results = await replay_single(conv, extractor, service, dry_run=args.dry_run)
        all_extracted.extend(results)
        # 避免请求过快
        await asyncio.sleep(1)

    # 汇总
    logger.info("=" * 60)
    logger.info(f"重放完成! 共处理 {len(conversations)} 条对话, 提取 {len(all_extracted)} 条数据")

    # 按类型统计
    type_counts: dict[str, int] = {}
    for item in all_extracted:
        t = item["type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    for t, c in type_counts.items():
        logger.info(f"  {t}: {c} 条")

    if not args.dry_run and all_extracted:
        logger.info("数据已写入 health_data.db")


if __name__ == "__main__":
    asyncio.run(main())
