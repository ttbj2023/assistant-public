"""对话业务服务.

提供对话相关的业务逻辑封装,包括对话创建,轮次管理,搜索等功能.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, override

from src.storage.dao.async_conversation_dao import AsyncConversationIndexDAO
from src.storage.models.conversation import ConversationIndex, ConversationIndexGroup

from .health_check_mixin import ServiceHealthCheckMixin


class ConversationService(ServiceHealthCheckMixin):
    """对话业务服务.

    负责对话相关的业务逻辑:
    - 对话创建和验证
    - 轮次号分配和管理
    - 对话内容索引和搜索
    - 对话统计和分析

    采用组合模式,使用通用功能组件.
    """

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        """初始化对话服务.

        Args:
            session_factory: SQLAlchemy异步会话工厂

        """
        super().__init__()
        self.session_factory = session_factory
        self.logger = logging.getLogger(f"{__name__}.ConversationService")

        # 组合DAO
        self.conversation_dao = AsyncConversationIndexDAO(session_factory)

    async def create_conversation(
        self,
        user_message: str,
        assistant_response: str,
        user_id: str,
        thread_id: str,
        agent_id: str,
        metadata: dict[str, Any] | None = None,
        round_number: int | None = None,
    ) -> ConversationIndex:
        """创建对话索引记录.

        支持预分配的轮次号或自动分配轮次号,确保 user_id + thread_id + agent_id + round_number 的唯一性.
        移除了 conversation_id 字段,使用 round_number 作为业务唯一标识.

        Args:
            user_message: 用户消息
            assistant_response: 助手回复
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID(必须, 由调用方显式传递)
            metadata: 额外的元数据
            round_number: 预分配的轮次号(可选),如果为None则自动分配

        Returns:
            创建的对话索引记录(包含使用的 round_number)

        Raises:
            ValueError: 当输入验证失败时
            RuntimeError: 当数据库操作失败时

        """
        start_time = time.time()

        try:
            self.logger.info(
                f"🚀 开始创建对话 - user_id: {user_id}, thread_id: {thread_id}, msg_len: {len(user_message)}, preallocated_round: {round_number}",
            )

            # 业务验证
            if not user_message.strip():
                raise ValueError("用户消息不能为空")
            if not assistant_response.strip():
                raise ValueError("助手回复不能为空")

            # 分配轮次号:如果提供了预分配的round_number则使用,否则自动分配
            if round_number is None:
                round_number = await self.allocate_round_number(user_id, thread_id)
                self.logger.debug("自动分配轮次号: %s", round_number)
            else:
                self.logger.debug("使用预分配轮次号: %s", round_number)

            async with self.session_factory() as session, session.begin():
                # 使用传入的 metadata (调用方应包含 timestamp)
                if metadata is None:
                    metadata = {}
                metadata.setdefault("timestamp", datetime.now(UTC).isoformat())
                metadata.setdefault("message_count", 2)

                conversation = await self.conversation_dao.store_index_data(
                    round_number=round_number,
                    content={
                        "user_message": user_message,
                        "assistant_response": assistant_response,
                    },
                    user_id=user_id,
                    thread_id=thread_id,
                    agent_id=agent_id,
                    metadata=metadata,
                )

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 创建对话完成 - 轮次: {round_number}, duration: {duration:.2f}ms",
            )
            return conversation

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 创建对话失败 - duration: {duration:.2f}ms, user_id: {user_id}, error: {e}",
                exc_info=True,
            )
            raise

    async def allocate_round_number(self, user_id: str, thread_id: str) -> int:
        """分配对话轮次号.

        Args:
            user_id: 用户ID
            thread_id: 线程ID

        Returns:
            新的轮次号

        Raises:
            RuntimeError: 当轮次号分配失败时

        """
        start_time = time.time()

        try:
            self.logger.info(
                "🔢 开始分配轮次号 - user_id: %s, thread_id: %s",
                user_id,
                thread_id,
            )

            async with (
                self.session_factory() as session,
                session.begin(),
            ):
                from sqlalchemy import func, select

                stmt = select(
                    func.coalesce(func.max(ConversationIndex.round_number), 0),
                ).where(
                    ConversationIndex.user_id == user_id,
                    ConversationIndex.thread_id == thread_id,
                )
                result = await session.execute(stmt)
                current_max = result.scalar() or 0
                new_round_number = current_max + 1

                self.logger.debug(
                    "🔍 轮次号分配成功 - user_id: %s, thread_id: %s, current_max: %s, new_round: %s",
                    user_id,
                    thread_id,
                    current_max,
                    new_round_number,
                )

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 分配轮次号完成 - 轮次: {new_round_number}, duration: {duration:.2f}ms",
            )
            return new_round_number

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 分配轮次号失败 - duration: {duration:.2f}ms, user_id: {user_id}, thread_id: {thread_id}, error: {e}",
                exc_info=True,
            )
            raise

    async def get_latest_round_number(self, user_id: str, thread_id: str) -> int:
        """获取最新轮次号.

        Args:
            user_id: 用户ID
            thread_id: 线程ID

        Returns:
            最新轮次号,如果没有对话则返回0

        """
        start_time = time.time()

        try:
            self.logger.info(
                "🔢 开始获取最新轮次号 - user_id: %s, thread_id: %s",
                user_id,
                thread_id,
            )

            async with self.session_factory() as session:
                from sqlalchemy import func, select

                stmt = select(
                    func.coalesce(func.max(ConversationIndex.round_number), 0),
                ).where(
                    ConversationIndex.user_id == user_id,
                    ConversationIndex.thread_id == thread_id,
                )
                result = await session.execute(stmt)
                round_number = result.scalar() or 0

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 获取最新轮次号完成 - 轮次: {round_number}, duration: {duration:.2f}ms",
            )
            return round_number

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 获取最新轮次号失败 - duration: {duration:.2f}ms, user_id: {user_id}, thread_id: {thread_id}, error: {e}",
                exc_info=True,
            )
            raise

    async def get_conversation_by_round(
        self,
        user_id: str,
        thread_id: str,
        round_number: int,
    ) -> ConversationIndex | None:
        """根据轮次号获取对话记录.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            round_number: 轮次号

        Returns:
            对话记录或None

        Raises:
            RuntimeError: 当数据库操作失败时

        """
        start_time = time.time()

        try:
            self.logger.info(
                "🔍 开始获取对话 - user_id: %s, thread_id: %s, round: %s",
                user_id,
                thread_id,
                round_number,
            )

            conversation = await self.conversation_dao.get_by_round_number(
                round_number,
                user_id,
                thread_id,
            )

            if conversation:
                duration = (time.time() - start_time) * 1000
                self.logger.info(
                    f"✅ 获取对话成功 - ID: {conversation.id}, duration: {duration:.2f}ms",
                )
            else:
                duration = (time.time() - start_time) * 1000
                self.logger.info(
                    f"对话不存在 - user_id: {user_id}, thread_id: {thread_id}, round: {round_number}, duration: {duration:.2f}ms",
                )

            return conversation

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 获取对话失败 - duration: {duration:.2f}ms, user_id: {user_id}, thread_id: {thread_id}, round: {round_number}, error: {e}",
                exc_info=True,
            )
            raise

    async def get_formatted_index_range(
        self,
        user_id: str,
        thread_id: str,
        start_round: int,
        end_round: int,
        format_template: str = "markdown",
    ) -> str:
        """获取格式化的索引范围.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            start_round: 开始轮次
            end_round: 结束轮次
            format_template: 格式模板

        Returns:
            格式化后的索引内容

        Raises:
            RuntimeError: 当数据库操作失败时

        """
        start_time = time.time()

        try:
            self.logger.info(
                "📝 开始获取格式化索引 - user_id: %s, thread_id: %s, range: %s-%s",
                user_id,
                thread_id,
                start_round,
                end_round,
            )

            formatted = await self.conversation_dao.get_formatted_index_range(
                user_id=user_id,
                thread_id=thread_id,
                start_round=start_round,
                end_round=end_round,
                format_template=format_template,
            )

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 获取格式化索引完成 - 输出长度: {len(formatted)}, duration: {duration:.2f}ms",
            )
            return formatted

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 获取格式化索引失败 - duration: {duration:.2f}ms, user_id: {user_id}, thread_id: {thread_id}, error: {e}",
                exc_info=True,
            )
            raise

    async def list_conversations(
        self,
        user_id: str,
        thread_id: str,
        limit: int = 100,
    ) -> list[ConversationIndex]:
        """列出对话记录.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            limit: 返回数量限制

        Returns:
            对话索引列表,按轮次号降序排列

        Raises:
            RuntimeError: 当数据库操作失败时

        """
        start_time = time.time()

        try:
            self.logger.info(
                "📋 开始列出对话 - user_id: %s, thread_id: %s, limit: %s",
                user_id,
                thread_id,
                limit,
            )

            conversations = await self.conversation_dao.list_conversations(
                user_id,
                thread_id,
                limit=limit,
            )

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 列出对话完成 - 数量: {len(conversations)}, duration: {duration:.2f}ms",
            )
            return conversations

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 列出对话失败 - duration: {duration:.2f}ms, user_id: {user_id}, thread_id: {thread_id}, error: {e}",
                exc_info=True,
            )
            raise

    async def get_conversations_in_range(
        self,
        start_round: int,
        end_round: int,
        user_id: str,
        thread_id: str,
    ) -> list[ConversationIndex]:
        """获取轮次范围内的对话索引 (按轮次号升序).

        Args:
            start_round: 起始轮次号 (包含)
            end_round: 结束轮次号 (包含)
            user_id: 用户ID
            thread_id: 线程ID

        Returns:
            范围内的对话索引列表

        """
        return await self.conversation_dao.get_conversations_in_range(
            start_round,
            end_round,
            user_id,
            thread_id,
        )

    async def list_recent_rounds(
        self,
        user_id: str,
        thread_id: str,
        limit: int = 10,
    ) -> list[int]:
        """获取最近的轮次号列表 (按轮次号降序).

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            limit: 最大返回数量

        Returns:
            轮次号列表, 最新在前

        """
        return await self.conversation_dao.list_recent_rounds(
            user_id,
            thread_id,
            limit=limit,
        )

    async def search_rounds_by_keywords(
        self,
        user_id: str,
        thread_id: str,
        terms: list[str],
        round_range: tuple[int, int] | None = None,
        limit: int = 30,
    ) -> list[int]:
        """关键词检索命中轮次号 (LIKE ANY 于 user_message/assistant_response).

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            terms: 已分词的查询词列表
            round_range: 可选轮次区间 (start, end), 闭区间
            limit: 最大返回数量

        Returns:
            命中轮次号列表, 按轮次号降序

        """
        return await self.conversation_dao.search_rounds_by_keywords(
            user_id,
            thread_id,
            terms,
            round_range=round_range,
            limit=limit,
        )

    async def get_conversations_by_rounds(
        self,
        user_id: str,
        thread_id: str,
        round_numbers: list[int],
    ) -> list[ConversationIndex]:
        """根据轮次号列表批量获取对话索引.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            round_numbers: 轮次号列表

        Returns:
            匹配的对话索引列表

        """
        return await self.conversation_dao.get_conversations_by_rounds(
            user_id,
            thread_id,
            round_numbers,
        )

    @override
    async def _check_service_health(self) -> dict[str, Any]:
        """检查对话服务健康状态.

        Returns:
            包含健康状态信息的字典

        """
        try:
            # 测试数据库连接
            async with self.session_factory() as session:
                # 尝试执行一个简单的查询来测试数据库连接
                from sqlalchemy import text

                await session.execute(text("SELECT 1"))

            # 获取对话统计信息
            stats = await self._get_conversation_statistics()

            return {
                "status": "healthy",
                "database_connected": True,
                "statistics": self._build_statistics(
                    total_conversations=stats.get("total_conversations", 0),
                    total_rounds=stats.get("total_rounds", 0),
                    latest_conversation_time=stats.get("latest_conversation_time"),
                    active_threads=stats.get("active_threads", 0),
                ),
                "error": None,
                "additional_info": {
                    "dao_accessible": True,
                },
            }

        except Exception as e:
            error_msg = f"对话服务健康检查失败: {e}"
            self.logger.error("❌ %s", error_msg, exc_info=True)

            return {
                "status": "unhealthy" if "connection" in str(e).lower() else "degraded",
                "database_connected": False,
                "statistics": {},
                "error": str(e),
                "additional_info": {
                    "dao_accessible": False,
                },
            }

    async def _get_conversation_statistics(self) -> dict[str, Any]:
        """获取对话统计信息.

        Returns:
            包含对话统计信息的字典

        """
        try:
            async with self.session_factory() as session:
                # 获取总对话数
                from sqlalchemy import text

                count_result = await session.execute(
                    text("SELECT COUNT(*) FROM conversation_index"),
                )
                total_conversations = count_result.scalar() or 0

                # 获取最大轮次号
                max_round_result = await session.execute(
                    text("SELECT MAX(round_number) FROM conversation_index"),
                )
                total_rounds = max_round_result.scalar() or 0

                # 获取最新对话时间
                latest_result = await session.execute(
                    text("SELECT MAX(updated_at) FROM conversation_index"),
                )
                latest_time = latest_result.scalar()

                # 获取活跃线程数(有对话记录的线程)
                thread_result = await session.execute(
                    text("SELECT COUNT(DISTINCT thread_id) FROM conversation_index"),
                )
                active_threads = thread_result.scalar() or 0

                return {
                    "total_conversations": total_conversations,
                    "total_rounds": total_rounds,
                    "latest_conversation_time": latest_time.isoformat()
                    if latest_time
                    else None,
                    "active_threads": active_threads,
                }

        except Exception as e:
            self.logger.warning("获取对话统计信息失败: %s", e)
            return {
                "total_conversations": 0,
                "total_rounds": 0,
                "latest_conversation_time": None,
                "active_threads": 0,
            }

    # ==================== 索引分组(老期冻结弧短语) ====================

    async def create_index_group(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
        round_start: int,
        round_end: int,
        arc_phrase: str,
    ) -> ConversationIndexGroup:
        """冻结一个语义 run 的弧短语分组.

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
        return await self.conversation_dao.create_group(
            user_id=user_id,
            thread_id=thread_id,
            agent_id=agent_id,
            round_start=round_start,
            round_end=round_end,
            arc_phrase=arc_phrase,
        )

    async def get_index_groups_up_to(
        self,
        user_id: str,
        thread_id: str,
        end_round: int,
    ) -> list[ConversationIndexGroup]:
        """获取老期冻结分组(round_end <= end_round, 按 round_start 升序).

        索引区老期读路径使用.
        """
        return await self.conversation_dao.get_groups_up_to(
            user_id=user_id,
            thread_id=thread_id,
            end_round=end_round,
        )


__all__ = ["ConversationService"]
