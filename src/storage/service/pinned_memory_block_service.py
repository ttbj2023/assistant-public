"""统一置顶记忆单一块服务层.

封装 DAO 访问 + 容量告警 (超限不拒绝, 仅 warning). 由 PinnedMemoryRewriter
(主模型覆写) 与注入层 (assembler / processor) 共用.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..dao.async_pinned_memory_block_dao import AsyncPinnedMemoryBlockDAO

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)

MAX_LINES = 20
MAX_TOTAL_LENGTH = 800


class PinnedMemoryBlockService:
    """统一置顶记忆单一块服务."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
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

    async def clear(self, user_id: str, thread_id: str) -> bool:
        """清空记忆块."""
        return await self._dao.delete(user_id, thread_id)

    async def get_formatted(self, user_id: str, thread_id: str) -> str:
        """供注入 system prompt 用的格式化文本 (空则空串, 由调用方决定是否注入)."""
        return await self.get_content(user_id, thread_id)


__all__ = ["PinnedMemoryBlockService"]
