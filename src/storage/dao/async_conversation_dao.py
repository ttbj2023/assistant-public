"""异步对话索引数据访问对象.

提供对对话索引数据表的特定异步数据访问操作.
基于组合模式设计,使用AsyncDatabaseOperations组件.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select, update

from ..formatters.conversation_formatter import create_conversation_formatter
from ..models.conversation import ConversationIndex, ConversationIndexGroup
from .database_operations import AsyncDatabaseOperations

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class AsyncConversationIndexDAO:
    """异步对话索引数据访问对象.

    使用组合模式,不再继承AsyncBaseDAO.
    提供对话索引相关的特定数据库操作.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        """初始化异步对话索引DAO.

        Args:
            session_factory: 数据库会话工厂

        """
        self.db_ops = AsyncDatabaseOperations(session_factory, ConversationIndex)
        self.group_db_ops = AsyncDatabaseOperations(
            session_factory, ConversationIndexGroup
        )
        self.session_factory = session_factory
        self.conversation_formatter = create_conversation_formatter()

    async def get_by_round_number(
        self,
        round_number: int,
        user_id: str | None = None,
        thread_id: str | None = None,
    ) -> ConversationIndex | None:
        """异步根据轮次号获取对话索引.

        Args:
            round_number: 轮次号
            user_id: 用户ID过滤
            thread_id: 线程ID过滤

        Returns:
            对话索引或None

        """
        try:
            filters: dict[str, Any] = {"round_number": round_number}
            if user_id is not None:
                filters["user_id"] = user_id
            if thread_id is not None:
                filters["thread_id"] = thread_id

            conversations = await self.db_ops.find_by_filters(filters, limit=1)
            return conversations[0] if conversations else None
        except Exception as e:
            logger.error("异步根据轮次号获取对话索引失败: %s", e)
            raise

    async def get_conversations_in_range(
        self,
        start_round: int,
        end_round: int,
        user_id: str | None = None,
        thread_id: str | None = None,
    ) -> list[ConversationIndex]:
        """异步获取轮次范围内的对话索引.

        Args:
            start_round: 起始轮次号
            end_round: 结束轮次号
            user_id: 用户ID过滤
            thread_id: 线程ID过滤

        Returns:
            范围内的对话索引列表

        """
        try:
            async with self.db_ops.session_factory() as session:
                statement = select(ConversationIndex).where(
                    ConversationIndex.round_number.between(start_round, end_round),
                )

                # 应用用户和线程过滤
                statement = self.db_ops.apply_user_thread_filters(
                    statement,
                    user_id,
                    thread_id,
                )

                statement = statement.order_by(ConversationIndex.round_number)
                result = await session.execute(statement)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("异步获取轮次范围内的对话索引失败: %s", e)
            raise

    async def get_round_range_by_time_range(
        self,
        user_id: str,
        thread_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> tuple[int, int] | None:
        """查 created_at 落 [start_time, end_time) 内的 MIN/MAX round_number.

        用于 time_filter(口语时间) → round_range(轮次区间) 转换:
        filter_parser 产出的 time_range 经此方法映射到检索路实际消费的 round_range.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            start_time: 区间起始 (包含)
            end_time: 区间结束 (不包含)

        Returns:
            (min_round, max_round) 或 None (区间内无对话)

        """
        try:
            async with self.db_ops.session_factory() as session:
                statement = select(
                    func.min(ConversationIndex.round_number),
                    func.max(ConversationIndex.round_number),
                ).where(
                    ConversationIndex.user_id == user_id,
                    ConversationIndex.thread_id == thread_id,
                    ConversationIndex.created_at >= start_time,
                    ConversationIndex.created_at < end_time,
                )
                result = await session.execute(statement)
                row = result.one()
                if row[0] is None:
                    return None
                return (row[0], row[1])
        except Exception as e:
            logger.error("按时间范围查询轮次区间失败: %s", e)
            raise

    async def update_conversation_index(
        self,
        user_id: str,
        thread_id: str,
        round_number: int,
        *,
        topic: str,
        summary: str,
    ) -> bool:
        """只更新索引元数据(topic/summary), 不碰基础内容字段.

        与 store_index_data_with_upsert(全量 UPSERT)互补: LLM 索引生成后独立写入
        topic/summary, 避免与基础内容存储路径竞争同一行导致 data race(全量覆盖擦除).

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            round_number: 轮次号(定位行)
            topic: 对话主题
            summary: 对话摘要

        Returns:
            True=行存在并更新, False=行不存在(未更新)

        """
        try:
            async with (
                self.db_ops.session_factory() as session,
                session.begin(),
            ):
                stmt = (
                    update(ConversationIndex)
                    .where(
                        ConversationIndex.user_id == user_id,
                        ConversationIndex.thread_id == thread_id,
                        ConversationIndex.round_number == round_number,
                    )
                    .values(
                        topic=topic,
                        summary=summary,
                        updated_at=datetime.now(UTC),
                    )
                )
                result = await session.execute(stmt)
                return result.rowcount > 0
        except Exception as e:
            logger.error("更新对话索引元数据失败: %s", e)
            raise

    async def list_conversations(
        self,
        user_id: str,
        thread_id: str,
        limit: int = 100,
    ) -> list[ConversationIndex]:
        """异步列出对话索引 (按轮次号降序).

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            limit: 返回数量限制

        Returns:
            对话索引列表, 最新轮次在前

        """
        try:
            async with self.db_ops.session_factory() as session:
                statement = select(ConversationIndex).where(
                    ConversationIndex.user_id == user_id,
                    ConversationIndex.thread_id == thread_id,
                )
                statement = statement.order_by(
                    ConversationIndex.round_number.desc(),
                ).limit(limit)
                result = await session.execute(statement)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("异步列出对话索引失败: %s", e)
            raise

    async def list_recent_rounds(
        self,
        user_id: str,
        thread_id: str,
        limit: int = 10,
    ) -> list[int]:
        """异步获取最近的轮次号列表 (按轮次号降序).

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            limit: 最大返回数量

        Returns:
            轮次号列表, 最新在前

        """
        try:
            async with self.db_ops.session_factory() as session:
                statement = select(ConversationIndex.round_number).where(
                    ConversationIndex.user_id == user_id,
                    ConversationIndex.thread_id == thread_id,
                )
                statement = statement.order_by(
                    ConversationIndex.round_number.desc(),
                ).limit(limit)
                result = await session.execute(statement)
                return [row[0] for row in result.all()]
        except Exception as e:
            logger.error("异步获取最近轮次号失败: %s", e)
            raise

    async def search_rounds_by_keywords(
        self,
        user_id: str,
        thread_id: str,
        terms: list[str],
        round_range: tuple[int, int] | None = None,
        limit: int = 30,
    ) -> list[int]:
        """异步关键词检索, 对 user_message/assistant_response 做 LIKE ANY.

        与向量语义检索互补: 精确匹配字面出现的词(尤其专有名词/实体名).
        任一 term 命中即入选(ANY 语义), 空词列表返回空.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            terms: 查询词列表(已分词, 建议长度>=2)
            round_range: 可选轮次区间 (start, end), 闭区间
            limit: 最大返回数量

        Returns:
            命中轮次号列表, 按轮次号降序

        """
        if not terms:
            return []
        try:
            clauses = []
            for term in terms:
                escaped = (
                    term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                pattern = f"%{escaped}%"
                clauses.append(
                    ConversationIndex.user_message.like(pattern, escape="\\")
                )
                clauses.append(
                    ConversationIndex.assistant_response.like(pattern, escape="\\")
                )

            async with self.db_ops.session_factory() as session:
                statement = select(ConversationIndex.round_number).where(
                    ConversationIndex.user_id == user_id,
                    ConversationIndex.thread_id == thread_id,
                    or_(*clauses),
                )
                if round_range is not None:
                    start_round, end_round = round_range
                    statement = statement.where(
                        ConversationIndex.round_number >= start_round,
                        ConversationIndex.round_number <= end_round,
                    )
                statement = statement.order_by(
                    ConversationIndex.round_number.desc(),
                ).limit(limit)
                result = await session.execute(statement)
                return [row[0] for row in result.all()]
        except Exception as e:
            logger.error("异步关键词检索失败: %s", e)
            raise

    async def get_latest_conversation(
        self,
        user_id: str | None = None,
        thread_id: str | None = None,
    ) -> ConversationIndex | None:
        """异步获取最新的对话索引.

        Args:
            user_id: 用户ID过滤
            thread_id: 线程ID过滤

        Returns:
            最新的对话索引或None

        """
        try:
            conversations = await self.db_ops.get_latest(
                user_id=user_id,
                thread_id=thread_id,
                order_field="round_number",
                limit=1,
            )
            return conversations[0] if conversations else None
        except Exception as e:
            logger.error("异步获取最新对话索引失败: %s", e)
            raise

    async def store_index_data(
        self,
        round_number: int | None = None,
        content: dict[str, Any] | None = None,
        user_id: str | None = None,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        agent_id: str,
    ) -> ConversationIndex:
        """异步存储对话索引数据.

        Args:
            round_number: 轮次号 (业务唯一标识)
            content: 对话内容, 包含 user_message 和 assistant_response
            user_id: 用户ID
            thread_id: 线程ID
            metadata: 元数据
            agent_id: Agent ID

        Returns:
            存储的对话索引

        """
        try:
            user_msg = (content or {}).get("user_message", "")
            asst_msg = (content or {}).get("assistant_response", "")

            return await self.store_index_data_with_upsert(
                round_number=round_number,
                user_id=user_id,
                thread_id=thread_id,
                user_message=user_msg,
                assistant_response=asst_msg,
                metadata=metadata or {},
                topic=None,
                summary=None,
                agent_id=agent_id,
            )
        except Exception as e:
            logger.error("异步存储对话索引数据失败: %s", e)
            raise

    async def bulk_create(self, items: list[dict[str, Any]]) -> list[ConversationIndex]:
        """异步批量创建对话索引记录.

        Args:
            items: 对话索引字典列表

        Returns:
            创建的记录列表

        """
        try:
            processed_items = []
            for item in items:
                processed_items.append(item)

            return await self.db_ops.bulk_create(
                processed_items,
                required_fields=[
                    "user_id",
                    "thread_id",
                    "round_number",
                    "user_message",
                ],
            )
        except Exception as e:
            logger.error("异步批量创建对话索引失败: %s", e)
            raise

    async def health_check(self) -> bool:
        """健康检查."""
        return await self.db_ops.health_check()

    async def get_latest_round_number(
        self,
        user_id: str | None = None,
        thread_id: str | None = None,
    ) -> int | None:
        """异步获取最新轮次号.

        Args:
            user_id: 用户ID过滤
            thread_id: 线程ID过滤

        Returns:
            最新轮次号或None

        """
        try:
            latest_conversation = await self.get_latest_conversation(
                user_id=user_id,
                thread_id=thread_id,
            )
            return latest_conversation.round_number if latest_conversation else None
        except Exception as e:
            logger.error("异步获取最新轮次号失败: %s", e)
            raise

    async def get_conversations_by_rounds(
        self,
        user_id: str,
        thread_id: str,
        round_numbers: list[int],
        limit: int = 100,
    ) -> list[ConversationIndex]:
        """异步根据轮次号列表批量获取对话索引.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            round_numbers: 轮次号列表
            limit: 返回数量限制

        Returns:
            匹配的对话索引列表

        """
        try:
            if not round_numbers:
                return []

            async with self.db_ops.session_factory() as session:
                statement = select(ConversationIndex).where(
                    ConversationIndex.round_number.in_(round_numbers),
                )

                # 应用用户和线程过滤
                statement = self.db_ops.apply_user_thread_filters(
                    statement,
                    user_id,
                    thread_id,
                )

                statement = statement.order_by(ConversationIndex.round_number)
                statement = statement.limit(limit)

                result = await session.execute(statement)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("异步根据轮次号列表批量获取对话索引失败: %s", e)
            raise

    async def get_conversation_rounds_by_range(
        self,
        user_id: str,
        thread_id: str,
        start_round: int,
        end_round: int,
    ) -> list[dict[str, Any]]:
        """按轮次范围获取对话数据.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            start_round: 起始轮次号(包含)
            end_round: 结束轮次号(包含)

        Returns:
            指定范围内的对话轮次列表

        """
        try:
            async with self.db_ops.session_factory() as session:
                statement = (
                    select(ConversationIndex)
                    .where(
                        ConversationIndex.user_id == user_id,
                        ConversationIndex.thread_id == thread_id,
                        ConversationIndex.round_number >= start_round,
                        ConversationIndex.round_number <= end_round,
                    )
                    .order_by(ConversationIndex.round_number.asc())
                )

                result = await session.execute(statement)
                conversations = result.scalars().all()

                conversation_rounds = []
                for conv in conversations:
                    conversation_rounds.append({
                        "round_number": conv.round_number,
                        "user_message": conv.user_message,
                        "assistant_response": conv.assistant_response,
                        "created_at": conv.created_at.isoformat()
                        if conv.created_at
                        else None,
                        "updated_at": conv.updated_at.isoformat()
                        if conv.updated_at
                        else None,
                    })

                return conversation_rounds
        except Exception as e:
            logger.error("按范围获取对话轮次失败: %s", e)
            raise

    async def store_index_data_with_upsert(
        self,
        round_number: int,
        user_message: str,
        assistant_response: str,
        user_id: str,
        thread_id: str,
        metadata: dict[str, Any] | None = None,
        topic: str | None = None,
        summary: str | None = None,
        *,
        agent_id: str,
    ) -> ConversationIndex:
        """使用UPSERT操作存储对话索引数据,避免UNIQUE约束冲突.

        Args:
            round_number: 轮次号(业务唯一标识)
            user_message: 用户消息
            assistant_response: 助手回复
            user_id: 用户ID
            thread_id: 线程ID
            metadata: 元数据
            topic: 对话主题
            summary: 对话摘要
            agent_id: Agent ID

        Returns:
            存储的对话索引

        Note:
            移除 conversation_id 参数,使用 round_number 作为唯一标识

        """
        if metadata is None:
            metadata = {}

        # 从metadata中提取topic,summary字段(如果传入参数为空)
        extracted_topic = topic
        extracted_summary = summary

        # 如果直接参数为空,尝试从metadata中提取
        if not extracted_topic and "topic" in metadata:
            extracted_topic = metadata["topic"]
        if not extracted_summary and "summary" in metadata:
            extracted_summary = metadata["summary"]

        try:
            async with self.db_ops.transaction_scope() as session:
                # 首先尝试查找现有记录
                stmt = select(ConversationIndex).where(
                    ConversationIndex.user_id == user_id,
                    ConversationIndex.thread_id == thread_id,
                    ConversationIndex.round_number == round_number,
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    # 更新现有记录 - 使用提取出的字段
                    existing.user_message = user_message
                    existing.assistant_response = assistant_response
                    if extracted_topic is not None:
                        existing.topic = extracted_topic
                    if extracted_summary is not None:
                        existing.summary = extracted_summary
                    existing.updated_at = datetime.now(UTC)

                    await session.flush()
                    await session.refresh(existing)
                    logger.debug(
                        "更新现有对话索引: %s:%s:%s",
                        user_id,
                        thread_id,
                        round_number,
                    )

                    return existing

                # 创建新记录 - 使用提取出的字段
                new_index = ConversationIndex(
                    round_number=round_number,
                    user_message=user_message,
                    assistant_response=assistant_response,
                    topic=extracted_topic,
                    summary=extracted_summary,
                    user_id=user_id,
                    thread_id=thread_id,
                    agent_id=agent_id,
                )

                session.add(new_index)
                await session.flush()
                await session.refresh(new_index)
                logger.debug(
                    "创建新对话索引: %s:%s:%s",
                    user_id,
                    thread_id,
                    round_number,
                )

                return new_index

        except Exception as e:
            logger.error("UPSERT存储对话索引数据失败: %s", e)
            raise

    # ==================== 格式化接口 - 将格式化逻辑从应用层下沉到存储层 ====================

    async def get_formatted_conversation_range(
        self,
        user_id: str,
        thread_id: str,
        start_round: int,
        end_round: int,
        format_template: str = "markdown",
    ) -> str:
        """获取指定范围的格式化对话历史.

        这是新的存储层格式化接口,将格式化逻辑从应用层下沉到存储层,
        提供高效的[x,x+n]范围查询和格式化功能.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            start_round: 起始轮次号(包含)
            end_round: 结束轮次号(包含)
            format_template: 格式化模板,目前仅支持 "markdown"

        Returns:
            格式化后的对话历史字符串

        """
        try:
            logger.debug(
                "获取格式化对话范围: %s:%s [%s-%s]",
                user_id,
                thread_id,
                start_round,
                end_round,
            )

            conversation_rounds = await self.get_conversation_rounds_by_range(
                user_id,
                thread_id,
                start_round,
                end_round,
            )

            # 使用存储层格式化器进行格式化
            formatted_result = (
                await self.conversation_formatter.format_conversation_range(
                    conversation_rounds,
                    format_template,
                )
            )

            logger.debug(f"格式化对话范围完成,输出长度: {len(formatted_result)}")
            return formatted_result

        except Exception as e:
            logger.error("获取格式化对话范围失败: %s", e)
            return ""

    async def get_formatted_index_range(
        self,
        user_id: str,
        thread_id: str,
        start_round: int,
        end_round: int,
        format_template: str = "markdown",
    ) -> str:
        """获取指定范围的格式化索引数据.

        这是新的存储层格式化接口,将索引格式化逻辑从应用层下沉到存储层.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            start_round: 起始轮次号(包含)
            end_round: 结束轮次号(包含)
            format_template: 格式化模板,目前仅支持 "markdown"

        Returns:
            格式化后的索引区字符串

        """
        try:
            logger.debug(
                "获取格式化索引范围: %s:%s [%s-%s]",
                user_id,
                thread_id,
                start_round,
                end_round,
            )

            index_conversations = await self.get_conversations_in_range(
                start_round,
                end_round,
                user_id,
                thread_id,
            )

            # 转换为索引区字典格式
            index_data = []
            for conv in index_conversations:
                index_data.append({
                    "round_number": conv.round_number,
                    "topic": conv.topic,
                    "summary": conv.summary,
                    "created_at": conv.created_at.isoformat()
                    if conv.created_at
                    else None,
                    "updated_at": conv.updated_at.isoformat()
                    if conv.updated_at
                    else None,
                })

            # 使用存储层格式化器进行格式化
            formatted_result = await self.conversation_formatter.format_index_range(
                index_data,
                format_template,
            )

            logger.debug(f"格式化索引范围完成,输出长度: {len(formatted_result)}")
            return formatted_result

        except Exception as e:
            logger.error("获取格式化索引范围失败: %s", e)
            return ""

    # ==================== 索引分组(老期冻结弧短语) ====================

    async def create_group(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
        round_start: int,
        round_end: int,
        arc_phrase: str,
    ) -> ConversationIndexGroup:
        """冻结一个语义 run 的弧短语分组(一次性写入, 永不再压缩).

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID(溯源)
            round_start: run 起始轮次(包含)
            round_end: run 结束轮次(包含)
            arc_phrase: LLM 蒸馏的弧短语

        Returns:
            创建的分组记录

        """
        try:
            return await self.group_db_ops.create(
                user_id=user_id,
                thread_id=thread_id,
                agent_id=agent_id,
                round_start=round_start,
                round_end=round_end,
                arc_phrase=arc_phrase,
            )
        except Exception as e:
            logger.error("冻结索引分组失败 [%s-%s]: %s", round_start, round_end, e)
            raise

    async def get_groups_up_to(
        self,
        user_id: str,
        thread_id: str,
        end_round: int,
    ) -> list[ConversationIndexGroup]:
        """获取老期冻结分组(round_end <= end_round, 按 round_start 升序).

        索引区老期读路径使用: 这些分组的弧短语构成早期对话的时间线.
        straddle 分组(round_end > end_round)不返回, 其老期部分由近期 fine 行覆盖.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            end_round: 上界轮次(索引区边界 index_end)

        Returns:
            冻结分组列表, 按 round_start 升序

        """
        try:
            async with self.group_db_ops.session_factory() as session:
                statement = (
                    select(ConversationIndexGroup)
                    .where(
                        ConversationIndexGroup.user_id == user_id,
                        ConversationIndexGroup.thread_id == thread_id,
                        ConversationIndexGroup.round_end <= end_round,
                    )
                    .order_by(ConversationIndexGroup.round_start.asc())
                )
                result = await session.execute(statement)
                return list(result.scalars().all())
        except Exception as e:
            logger.error("获取索引分组失败 [<= %s]: %s", end_round, e)
            raise
