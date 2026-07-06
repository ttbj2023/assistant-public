"""用户要求记事本服务层.

封装 DAO 访问 + 限额校验 (≤10 行 / ≤500 字). 由 requirement_memory 工具
(主对话模型全文重写) 与 MemoryAssembler (注入 system prompt) 共用.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..dao.async_user_requirement_dao import AsyncUserRequirementDAO

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)

# 限额: 防止上下文膨胀与垃圾堆积. 超限拒绝写入, 迫使模型只留最重要的要求.
MAX_LINES = 10
MAX_TOTAL_LENGTH = 500


class UserRequirementService:
    """用户要求记事本服务."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._dao = AsyncUserRequirementDAO(session_factory)

    @staticmethod
    def _validate(content: str) -> None:
        """校验限额: 非空行数 ≤ MAX_LINES, 总长 ≤ MAX_TOTAL_LENGTH."""
        lines = [ln for ln in content.splitlines() if ln.strip()]
        if len(lines) > MAX_LINES:
            raise ValueError(
                f"要求条数 {len(lines)} 超过上限 {MAX_LINES}, 请只保留最重要的要求",
            )
        if len(content) > MAX_TOTAL_LENGTH:
            raise ValueError(
                f"要求总长 {len(content)} 超过上限 {MAX_TOTAL_LENGTH} 字, 请精简",
            )

    async def get_content(self, user_id: str, thread_id: str) -> str:
        """读取完整要求列表 (空则返回空串)."""
        record = await self._dao.get(user_id, thread_id)
        return record.content if record else ""

    async def set_content(
        self,
        user_id: str,
        thread_id: str,
        content: str,
    ) -> str:
        """全文覆盖写入 (空串=清空). 写前校验限额.

        Raises:
            ValueError: 条数或总长超限

        """
        content = content or ""
        if content:
            self._validate(content)
        await self._dao.upsert(user_id, thread_id, content)
        return content

    async def clear(self, user_id: str, thread_id: str) -> bool:
        """清空记事本."""
        return await self._dao.delete(user_id, thread_id)

    async def get_formatted(self, user_id: str, thread_id: str) -> str:
        """供注入 system prompt 用的格式化文本 (空则空串, 由调用方决定是否注入)."""
        return await self.get_content(user_id, thread_id)


__all__ = ["UserRequirementService"]
