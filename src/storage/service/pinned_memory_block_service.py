"""统一置顶记忆单一块服务层.

封装 DAO 访问 + 容量告警 (超限不拒绝, 仅 warning). 由 PinnedMemoryRewriter
(主模型覆写) 与注入层 (assembler / processor) 共用.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, override

from sqlalchemy import text

from src.inference.content_analyzer.pinned_memory_rewriter import (
    MAX_LINES,
    MAX_TOTAL_LENGTH,
)

from ..dao.async_pinned_memory_block_dao import AsyncPinnedMemoryBlockDAO
from .health_check_mixin import ServiceHealthCheckMixin

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class PinnedMemoryBlockService(ServiceHealthCheckMixin):
    """统一置顶记忆单一块服务."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        ServiceHealthCheckMixin.__init__(self)
        self._session_factory = session_factory
        self._dao = AsyncPinnedMemoryBlockDAO(session_factory)

    @staticmethod
    def check_capacity(content: str) -> bool:
        """检查内容是否在容量限额内.

        Returns:
            True=合法, False=超限
        """
        if not content:
            return True
        lines = [ln for ln in content.splitlines() if ln.strip()]
        if len(lines) > MAX_LINES:
            return False
        return len(content) <= MAX_TOTAL_LENGTH

    async def get_content(self, user_id: str, thread_id: str) -> str:
        """读取完整记忆块 (空则返回空串)."""
        record = await self._dao.get(user_id, thread_id)
        return record.content if record else ""

    async def set_content(
        self,
        user_id: str,
        thread_id: str,
        content: str,
    ) -> str:
        """全文覆盖写入 (空串=清空). 超限告警但不拒绝.

        主模型覆写信任: prompt 已约束容量, 此处兜底告警便于发现问题.
        """
        content = content or ""
        if content and not self.check_capacity(content):
            lines = [ln for ln in content.splitlines() if ln.strip()]
            if len(lines) > MAX_LINES:
                logger.warning(
                    "统一置顶记忆块条数 %d 超过上限 %d, 已写入但建议精简",
                    len(lines),
                    MAX_LINES,
                )
            elif len(content) > MAX_TOTAL_LENGTH:
                logger.warning(
                    "统一置顶记忆块总长 %d 超过上限 %d, 已写入但建议精简",
                    len(content),
                    MAX_TOTAL_LENGTH,
                )
        await self._dao.upsert(user_id, thread_id, content)
        return content

    async def get_formatted(self, user_id: str, thread_id: str) -> str:
        """供注入 system prompt 用的格式化文本 (空则空串, 由调用方决定是否注入)."""
        return await self.get_content(user_id, thread_id)

    @override
    async def _check_service_health(self) -> dict[str, Any]:
        """置顶记忆块健康检查 (统计 pinned_memory_block 表)."""
        try:
            statistics = await self._get_statistics()
            return {
                "status": "healthy",
                "database_connected": True,
                "statistics": statistics,
                "error": None,
                "additional_info": {"dao_accessible": True},
            }
        except Exception as e:
            self.logger.error("❌ 置顶记忆块健康检查失败: %s", e, exc_info=True)
            return {
                "status": "unhealthy" if "connection" in str(e).lower() else "degraded",
                "database_connected": False,
                "statistics": {},
                "error": str(e),
                "additional_info": {"dao_accessible": False},
            }

    async def _get_statistics(self) -> dict[str, Any]:
        """获取置顶记忆块统计信息."""
        try:
            async with self._session_factory() as session:
                count_result = await session.execute(
                    text("SELECT COUNT(*) FROM pinned_memory_block"),
                )
                total_records = count_result.scalar() or 0

                nonempty_result = await session.execute(
                    text(
                        "SELECT COUNT(*) FROM pinned_memory_block "
                        "WHERE content IS NOT NULL AND content != ''"
                    ),
                )
                nonempty_records = nonempty_result.scalar() or 0

                latest_result = await session.execute(
                    text("SELECT MAX(updated_at) FROM pinned_memory_block"),
                )
                latest_time = latest_result.scalar()

                user_result = await session.execute(
                    text("SELECT COUNT(DISTINCT user_id) FROM pinned_memory_block"),
                )
                total_users = user_result.scalar() or 0

                thread_result = await session.execute(
                    text("SELECT COUNT(DISTINCT thread_id) FROM pinned_memory_block"),
                )
                total_threads = thread_result.scalar() or 0

                return {
                    "total_records": total_records,
                    "nonempty_records": nonempty_records,
                    "latest_memory_time": latest_time.isoformat()
                    if latest_time
                    else None,
                    "total_users": total_users,
                    "total_threads": total_threads,
                }
        except Exception as e:
            logger.warning("获取置顶记忆块统计信息失败: %s", e)
            return {
                "total_records": 0,
                "nonempty_records": 0,
                "latest_memory_time": None,
                "total_users": 0,
                "total_threads": 0,
            }


__all__ = ["PinnedMemoryBlockService"]
