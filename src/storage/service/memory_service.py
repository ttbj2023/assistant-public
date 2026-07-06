"""记忆业务服务.

提供记忆相关的业务逻辑封装,包括置顶记忆,记忆检索等功能.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, override

from src.storage.dao.async_simple_pinned_memory_dao import AsyncSimplePinnedMemoryDAO
from src.storage.formatters.pinned_memory_formatter import (
    create_pinned_memory_formatter,
)
from src.storage.models.simple_pinned_memory import (
    SimplePinnedMemory,
    SimplePinnedMemoryType,
)

from .health_check_mixin import ServiceHealthCheckMixin


class MemoryService(ServiceHealthCheckMixin):
    """记忆业务服务.

    负责记忆相关的业务逻辑:
    - 置顶记忆内容验证和格式化
    - 记忆类型管理
    - 记忆更新策略
    - 记忆检索和分析

    采用组合模式,使用通用功能组件.
    """

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        """初始化记忆服务.

        Args:
            session_factory: SQLAlchemy异步会话工厂

        """
        super().__init__()
        self.session_factory = session_factory
        self.logger = logging.getLogger(f"{__name__}.MemoryService")

        # 组合DAO
        self.memory_dao = AsyncSimplePinnedMemoryDAO(session_factory)
        self.pinned_memory_formatter = create_pinned_memory_formatter()

    async def get_pinned_memory_as_dict(
        self,
        user_id: str,
        thread_id: str,
    ) -> dict[str, str]:
        """获取2字段置顶记忆字典(未加标题,原始内容)."""
        memories = await self.memory_dao.get_all_memories(user_id, thread_id)

        content_by_type: dict[str, str] = {
            "basic_info": "",
            "preferences": "",
        }
        for memory in memories:
            if memory.memory_type == SimplePinnedMemoryType.BASIC_INFO:
                content_by_type["basic_info"] = memory.content or ""
            elif memory.memory_type == SimplePinnedMemoryType.PREFERENCES:
                content_by_type["preferences"] = memory.content or ""

        return content_by_type

    async def format_pinned_memory_dict(
        self,
        pinned_memory_dict: dict[str, str] | None,
        format_template: str = "markdown",
    ) -> str:
        """格式化置顶记忆字典为字符串(不触发数据库读取)."""
        sanitized = self.pinned_memory_formatter.sanitize_pinned_memory_data(
            pinned_memory_dict or {},
        )
        return await self.pinned_memory_formatter.format_pinned_memory(
            sanitized,
            format_template,
        )

    async def update_memory(
        self,
        memory_type: SimplePinnedMemoryType,
        content: str,
        user_id: str,
        thread_id: str,
    ) -> SimplePinnedMemory:
        """更新置顶记忆(包含内容验证).

        Args:
            memory_type: 记忆类型
            content: 记忆内容
            user_id: 用户ID
            thread_id: 线程ID
            **metadata: 额外的元数据

        Returns:
            更新后的记忆记录

        Raises:
            ValueError: 当输入验证失败时
            RuntimeError: 当数据库操作失败时

        """
        start_time = time.time()
        try:
            self.logger.info(
                f"🚀 开始更新记忆 - user_id: {user_id}, thread_id: {thread_id}, type: {memory_type.value}, content_len: {len(content)}",
            )

            # 业务验证
            validation_result = self._validate_memory_content(memory_type, content)
            if not validation_result["valid"]:
                raise ValueError(f"记忆内容验证失败: {validation_result['errors']}")

            # 格式化内容
            formatted_content = self._format_memory_content(memory_type, content)

            async with (
                self.session_factory() as session,
                session.begin(),
            ):
                # 更新或插入记忆
                memory = await self.memory_dao.upsert_memory(
                    user_id=user_id,
                    thread_id=thread_id,
                    memory_type=memory_type,
                    content=formatted_content,
                )

                self.logger.info(
                    f"✅ 记忆更新成功 - user_id: {user_id}, thread_id: {thread_id}, memory_type: {memory_type.value}, memory_id: {memory.id}",
                )

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 更新记忆完成 - duration: {duration:.2f}ms, ID: {memory.id}",
            )
            return memory

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 更新记忆失败 - duration: {duration:.2f}ms, user_id: {user_id}, thread_id: {thread_id}, memory_type: {memory_type.value}, error: {e}",
                exc_info=True,
            )
            raise

    async def get_memory_by_type(
        self,
        user_id: str,
        thread_id: str,
        memory_type: SimplePinnedMemoryType,
    ) -> SimplePinnedMemory | None:
        """根据类型获取记忆.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            memory_type: 记忆类型

        Returns:
            记忆记录,如果不存在则返回None

        """
        start_time = time.time()
        try:
            self.logger.info(
                f"🚀 开始获取记忆 - user_id: {user_id}, thread_id: {thread_id}, type: {memory_type.value}",
            )

            async with self.session_factory():
                memory = await self.memory_dao.get_memory_by_type(
                    user_id,
                    thread_id,
                    memory_type,
                )

            result_summary = "找到" if memory else "未找到"
            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 获取记忆完成 - duration: {duration:.2f}ms, result: {result_summary}",
            )

            return memory

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 获取记忆失败 - duration: {duration:.2f}ms, user_id: {user_id}, thread_id: {thread_id}, memory_type: {memory_type.value}, error: {e}",
                exc_info=True,
            )
            raise

    async def get_all_memories(
        self,
        user_id: str,
        thread_id: str,
    ) -> list[SimplePinnedMemory]:
        """获取用户的所有记忆.

        Args:
            user_id: 用户ID
            thread_id: 线程ID

        Returns:
            所有记忆记录列表

        """
        start_time = time.time()
        try:
            self.logger.info(
                "🚀 开始获取所有记忆 - user_id: %s, thread_id: %s",
                user_id,
                thread_id,
            )

            async with self.session_factory():
                memories = await self.memory_dao.get_all_memories(user_id, thread_id)

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 获取所有记忆完成 - duration: {duration:.2f}ms, count: {len(memories)}",
            )
            return memories

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 获取所有记忆失败 - duration: {duration:.2f}ms, user_id: {user_id}, thread_id: {thread_id}, error: {e}",
                exc_info=True,
            )
            raise

    async def delete_memory(
        self,
        user_id: str,
        thread_id: str,
        memory_type: SimplePinnedMemoryType,
    ) -> bool:
        """删除指定类型的记忆.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            memory_type: 记忆类型

        Returns:
            删除是否成功

        """
        start_time = time.time()
        try:
            self.logger.info(
                f"🚀 开始删除记忆 - user_id: {user_id}, thread_id: {thread_id}, type: {memory_type.value}",
            )

            async with self.session_factory() as session, session.begin():
                success = await self.memory_dao.delete_memory(
                    user_id,
                    thread_id,
                    memory_type,
                )

            result_summary = "成功" if success else "失败"
            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 删除记忆完成 - duration: {duration:.2f}ms, result: {result_summary}",
            )

            return success

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 删除记忆失败 - duration: {duration:.2f}ms, user_id: {user_id}, thread_id: {thread_id}, memory_type: {memory_type.value}, error: {e}",
                exc_info=True,
            )
            raise

    def _validate_memory_content(
        self,
        memory_type: SimplePinnedMemoryType,
        content: str,
    ) -> dict:
        """验证记忆内容.

        Args:
            memory_type: 记忆类型
            content: 记忆内容

        Returns:
            验证结果字典

        """
        errors = []

        # 基本验证
        if not content or not content.strip():
            errors.append("记忆内容不能为空")

        # 根据类型进行特定验证
        if memory_type == SimplePinnedMemoryType.BASIC_INFO and len(content) > 2000:
            errors.append("基本信息内容过长(最大2000字符)")
        elif memory_type == SimplePinnedMemoryType.PREFERENCES and len(content) > 1000:
            errors.append("偏好信息内容过长(最大1000字符)")

        return {"valid": len(errors) == 0, "errors": errors}

    def _format_memory_content(
        self,
        memory_type: SimplePinnedMemoryType,
        content: str,
    ) -> str:
        """格式化记忆内容.

        Args:
            memory_type: 记忆类型
            content: 原始内容

        Returns:
            格式化后的内容

        """
        # 清理内容:去除首尾空白,统一换行符
        cleaned_content = content.strip().replace("\r\n", "\n").replace("\r", "\n")

        # 根据类型进行特定格式化
        if memory_type == SimplePinnedMemoryType.BASIC_INFO:
            # 基本信息:保持段落结构
            paragraphs = [p.strip() for p in cleaned_content.split("\n\n") if p.strip()]
            formatted_content = "\n\n".join(paragraphs)

        elif memory_type == SimplePinnedMemoryType.PREFERENCES:
            # 偏好信息:可以用逗号分隔或换行分隔
            lines = [
                line.strip() for line in cleaned_content.split("\n") if line.strip()
            ]
            formatted_content = "\n".join(lines)

        else:
            formatted_content = cleaned_content

        return formatted_content

    @override
    async def _check_service_health(self) -> dict[str, Any]:
        """检查记忆服务健康状态.

        Returns:
            包含健康状态信息的字典

        """
        try:
            # 测试数据库连接
            async with self.session_factory() as session:
                # 尝试执行一个简单的查询来测试数据库连接
                from sqlalchemy import text

                await session.execute(text("SELECT 1"))

            # 获取记忆统计信息
            stats = await self._get_memory_statistics()

            return {
                "status": "healthy",
                "database_connected": True,
                "statistics": self._build_statistics(
                    total_memories=stats.get("total_memories", 0),
                    by_type=stats.get("by_type", {}),
                    latest_memory_time=stats.get("latest_memory_time"),
                    total_users=stats.get("total_users", 0),
                    total_threads=stats.get("total_threads", 0),
                    memory_types_supported=stats.get("memory_types_supported", []),
                ),
                "error": None,
                "additional_info": {
                    "dao_accessible": True,
                    "formatter_accessible": self.pinned_memory_formatter is not None,
                },
            }

        except Exception as e:
            error_msg = f"记忆服务健康检查失败: {e}"
            self.logger.error("❌ %s", error_msg, exc_info=True)

            return {
                "status": "unhealthy" if "connection" in str(e).lower() else "degraded",
                "database_connected": False,
                "statistics": {},
                "error": str(e),
                "additional_info": {
                    "dao_accessible": False,
                    "formatter_accessible": self.pinned_memory_formatter is not None,
                },
            }

    async def _get_memory_statistics(self) -> dict[str, Any]:
        """获取记忆统计信息.

        Returns:
            包含记忆统计信息的字典

        """
        try:
            async with self.session_factory() as session:
                # 获取总记忆数
                from sqlalchemy import text

                count_result = await session.execute(
                    text("SELECT COUNT(*) FROM simple_pinned_memory"),
                )
                total_memories = count_result.scalar() or 0

                # 按类型统计
                type_result = await session.execute(
                    text(
                        "SELECT memory_type, COUNT(*) FROM simple_pinned_memory GROUP BY memory_type",
                    ),
                )
                by_type = {row[0]: row[1] for row in type_result.fetchall()}

                # 获取最新记忆时间
                latest_result = await session.execute(
                    text("SELECT MAX(updated_at) FROM simple_pinned_memory"),
                )
                latest_time = latest_result.scalar()

                # 获取用户数(有记忆记录的唯一用户数)
                user_result = await session.execute(
                    text("SELECT COUNT(DISTINCT user_id) FROM simple_pinned_memory"),
                )
                total_users = user_result.scalar() or 0

                # 获取线程数(有记忆记录的唯一线程数)
                thread_result = await session.execute(
                    text("SELECT COUNT(DISTINCT thread_id) FROM simple_pinned_memory"),
                )
                total_threads = thread_result.scalar() or 0

                # 获取支持的记忆类型
                memory_types_supported = [t.value for t in SimplePinnedMemoryType]

                return {
                    "total_memories": total_memories,
                    "by_type": by_type,
                    "latest_memory_time": latest_time.isoformat()
                    if latest_time
                    else None,
                    "total_users": total_users,
                    "total_threads": total_threads,
                    "memory_types_supported": memory_types_supported,
                }

        except Exception as e:
            self.logger.warning("获取记忆统计信息失败: %s", e)
            return {
                "total_memories": 0,
                "by_type": {},
                "latest_memory_time": None,
                "total_users": 0,
                "total_threads": 0,
                "memory_types_supported": [t.value for t in SimplePinnedMemoryType],
            }
