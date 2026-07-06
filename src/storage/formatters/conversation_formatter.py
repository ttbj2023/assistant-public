"""对话格式化器.

将对话格式化逻辑从应用层下沉到存储层,提供高效的格式化接口.
支持范围查询和批量格式化处理.
"""

from __future__ import annotations

import logging
from typing import Any

from src.utils import (
    build_sections,
    create_conversation_round,
    format_date_short,
    validate_format_template,
)

logger = logging.getLogger(__name__)


class ConversationFormatter:
    """对话格式化器.

    负责在存储层对对话数据进行格式化处理,包括:
    - 单个对话轮次格式化
    - 批量对话历史格式化
    - 索引区格式化
    - 范围查询格式化
    """

    def __init__(self) -> None:
        """初始化对话格式化器."""
        logger.debug("🔧 初始化ConversationFormatter")

    async def format_conversation_range(
        self,
        conversation_rounds: list[dict[str, Any]],
        format_template: str = "markdown",
    ) -> str:
        """格式化指定范围的对话历史.

        Args:
            conversation_rounds: 对话轮次数据列表
            format_template: 格式化模板,目前仅支持 "markdown"

        Returns:
            格式化后的对话历史字符串

        """
        try:
            format_template = validate_format_template(format_template)

            if not conversation_rounds:
                return ""

            logger.debug(f"开始格式化 {len(conversation_rounds)} 轮对话数据")
            formatted_rounds = []

            for round_data in conversation_rounds:
                # 跳过无效数据
                if not round_data or not isinstance(round_data, dict):
                    logger.warning("⚠️ 跳过无效轮次数据: %s", round_data)
                    continue

                formatted_round = await self.format_single_round(
                    round_data,
                    format_template,
                )
                if formatted_round.strip():
                    round_number = round_data.get("round_number", 0)
                    # 使用统一的工具函数创建轮次格式
                    formatted_rounds.append(
                        create_conversation_round(round_number, formatted_round),
                    )

            result = build_sections(formatted_rounds, "\n\n---\n\n") or "暂无对话历史"
            logger.debug(f"对话格式化完成,输出长度: {len(result)}")
            return result

        except Exception as e:
            logger.error("格式化对话范围失败: %s", e, exc_info=True)
            raise ValueError(f"对话范围格式化失败: {e}") from e

    async def format_single_round(
        self,
        round_data: dict[str, Any],
        format_template: str = "markdown",
    ) -> str:
        """格式化单个对话轮次.

        Args:
            round_data: 对话轮次数据字典
            format_template: 格式化模板,目前仅支持 "markdown"

        Returns:
            格式化后的单个对话轮次字符串

        """
        try:
            format_template = validate_format_template(format_template)

            user_message = round_data.get("user_message", "").strip()
            assistant_response = round_data.get("assistant_response", "").strip()

            # 归档轮次: 如果正文已被移除, 则主对话区不输出该轮次.
            if not user_message and not assistant_response:
                return ""

            # 时间信息由 processor_orchestrator 在存入数据库前拼入 user_message,
            # 轮次号由外层 create_conversation_round 输出, 此处不再重复添加
            formatted_lines = []
            if user_message:
                formatted_lines.append(f"User: {user_message}")

            if assistant_response:
                formatted_lines.append(f"Assistant: {assistant_response}")

            return "\n".join(formatted_lines)

        except Exception as e:
            logger.error("格式化单个对话轮次失败: %s", e)
            return ""

    async def format_index_range(
        self,
        index_data: list[dict[str, Any]],
        format_template: str = "markdown",
    ) -> str:
        """格式化指定范围的索引数据.

        Args:
            index_data: 索引数据列表
            format_template: 格式化模板,目前仅支持 "markdown"

        Returns:
            格式化后的索引区字符串

        """
        try:
            if format_template != "markdown":
                logger.warning(
                    "不支持的格式模板: %s,使用默认markdown",
                    format_template,
                )
                format_template = "markdown"

            if not index_data:
                return ""

            logger.debug(f"开始格式化 {len(index_data)} 条索引数据")
            table_rows = []

            for index_item in index_data:
                round_number = index_item.get("round_number", 0)
                summary = index_item.get("summary") or ""
                topic = index_item.get("topic") or ""
                created_at = index_item.get("created_at") or ""

                time_str = format_date_short(created_at) if created_at else ""

                if len(summary) > 80:
                    summary = summary[:80] + "..."

                table_rows.append(
                    f"| {round_number} | {topic} | {summary} | {time_str} |",
                )

            # UNIT_TEST_EXEMPT: 防御代码, index_data非空时循环必添加行
            if not table_rows:
                return ""

            table_header = "| Round | Topic | Summary | Time |"
            table_separator = "|------|----------|----------|------|"

            result = (
                f"<index>\n{table_header}\n{table_separator}\n"
                + "\n".join(table_rows)
                + "\n</index>"
            )
            logger.debug(f"索引格式化完成,输出长度: {len(result)}")
            return result

        except Exception as e:
            logger.error("格式化索引范围失败: %s", e)
            return ""

    async def format_index_groups(
        self,
        groups_data: list[dict[str, Any]],
    ) -> str:
        """格式化老期冻结分组为时间线弧短语表.

        每行 = 一个已闭合语义 run 的弧短语(范围 + 叙事), 按时间顺序拼接
        构成早期对话的演变轨迹. 这是 LLM 获取早期对话时间连续性的主要来源.

        Args:
            groups_data: 分组数据列表 [{round_start, round_end, arc_phrase}, ...]

        Returns:
            格式化后的时间线字符串(空列表返回空串)

        """
        try:
            if not groups_data:
                return ""

            table_rows = []
            for g in groups_data:
                rs = g.get("round_start", 0)
                re_ = g.get("round_end", 0)
                arc = g.get("arc_phrase") or ""
                rng = f"{rs}-{re_}" if rs != re_ else f"{rs}"
                table_rows.append(f"| {rng} | {arc} |")

            # UNIT_TEST_EXEMPT: 防御代码, groups_data非空时循环必添加行
            if not table_rows:
                return ""

            table_header = "| 轮次 | 话题弧 |"
            table_separator = "|------|--------|"
            return (
                f"<timeline>\n{table_header}\n{table_separator}\n"
                + "\n".join(table_rows)
                + "\n</timeline>"
            )
        except Exception as e:
            logger.error("格式化索引分组失败: %s", e)
            return ""


# 工厂函数
def create_conversation_formatter() -> ConversationFormatter:
    """创建对话格式化器实例.

    Returns:
        对话格式化器实例

    """
    return ConversationFormatter()


# 导出
__all__ = ["ConversationFormatter", "create_conversation_formatter"]
