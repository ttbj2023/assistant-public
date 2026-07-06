"""异步TODO数据访问对象.

提供对TODO数据表的特定异步数据访问操作.
基于组合模式设计,使用AsyncDatabaseOperations组件.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..models.todo import TodoItem, TodoPriority, TodoStatus
from .database_operations import AsyncDatabaseOperations

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class AsyncTodoDAO:
    """异步TODO数据访问对象.

    使用组合模式,不再继承AsyncBaseDAO.
    提供TODO相关的特定数据库操作.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        """初始化异步TODO DAO.

        Args:
            session_factory: 数据库会话工厂

        """
        self.db_ops = AsyncDatabaseOperations(session_factory, TodoItem)
        self.session_factory = session_factory

    async def create_todo(
        self,
        title: str,
        user_id: str,
        thread_id: str,
        description: str | None = None,
        priority: TodoPriority = TodoPriority.MEDIUM,
        status: TodoStatus = TodoStatus.PENDING,
        due_date: datetime | None = None,
    ) -> TodoItem:
        """创建TODO项.

        Args:
            title: TODO标题
            user_id: 用户ID
            thread_id: 线程ID
            description: TODO描述
            priority: 优先级
            status: 状态
            due_date: 截止日期

        Returns:
            创建的TODO项

        """
        # 直接返回DAO创建的结果,避免session分离问题
        return await self.db_ops.create_with_validation(
            required_fields=["title", "user_id", "thread_id"],
            default_fields={
                "status": status,
                "priority": priority,
                "description": description,
                "due_date": due_date,
            },
            title=title,
            user_id=user_id,
            thread_id=thread_id,
            description=description,
            priority=priority,
            status=status,
            due_date=due_date,
        )

    async def get_todo_by_id(self, todo_id: int) -> TodoItem | None:
        """根据ID获取TODO项.

        Args:
            todo_id: TODO项ID

        Returns:
            TODO项或None

        """
        return await self.db_ops.get_by_id(todo_id)

    async def list_by_status(
        self,
        status: TodoStatus,
        limit: int = 100,
        user_id: str | None = None,
        thread_id: str | None = None,
    ) -> list[TodoItem]:
        """异步按状态列出TODO项.

        Args:
            status: TODO状态
            limit: 返回数量限制
            user_id: 用户ID过滤
            thread_id: 线程ID过滤

        Returns:
            TODO项列表

        """
        try:
            filters: dict[str, Any] = {"status": status}
            if user_id is not None:
                filters["user_id"] = user_id
            if thread_id is not None:
                filters["thread_id"] = thread_id

            return await self.db_ops.find_by_filters(filters, limit=limit)
        except Exception as e:
            logger.error("异步按状态查询TODO失败: %s", e)
            raise

    async def list_by_filters(
        self,
        limit: int = 100,
        offset: int = 0,
        **filters: Any,
    ) -> list[TodoItem]:
        """按任意字段组合过滤查询TODO项.

        Args:
            limit: 返回数量限制
            offset: 偏移量
            **filters: 过滤字段 (status, priority, user_id, thread_id 等)

        Returns:
            TODO项列表

        """
        try:
            return await self.db_ops.find_by_filters(
                filters,
                limit=limit,
                offset=offset,
            )
        except Exception as e:
            logger.error("异步过滤查询TODO失败: %s", e)
            raise

    async def update_todo(self, todo_id: int, **update_data: Any) -> TodoItem | None:
        """更新TODO项.

        Args:
            todo_id: TODO项ID
            **update_data: 更新数据

        Returns:
            更新后的TODO项或None

        """
        return await self.db_ops.update(todo_id, update_data)

    async def update_status(self, todo_id: int, status: TodoStatus) -> TodoItem | None:
        """更新TODO项状态.

        Args:
            todo_id: TODO项ID
            status: 新状态

        Returns:
            更新后的TODO项或None

        """
        return await self.update_todo(todo_id, status=status)

    async def delete_todo(self, todo_id: int) -> bool:
        """删除TODO项.

        Args:
            todo_id: TODO项ID

        Returns:
            是否删除成功

        """
        return await self.db_ops.delete_by_id(todo_id)

    async def bulk_create(self, items: list[dict[str, Any]]) -> list[TodoItem]:
        """异步批量创建TODO项.

        Args:
            items: TODO项字典列表

        Returns:
            创建的TODO项列表

        """
        return await self.db_ops.bulk_create(
            items,
            required_fields=["title", "user_id", "thread_id"],
            default_fields={
                "status": TodoStatus.PENDING,
                "priority": TodoPriority.MEDIUM,
            },
        )

    async def list_all(self, limit: int = 100, offset: int = 0) -> list[TodoItem]:
        """列出所有TODO项 - 保持与基类方法签名一致.

        Args:
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            TODO项列表

        """
        try:
            # 使用基类的标准实现
            return await self.db_ops.list_all(limit=limit, offset=offset)
        except Exception as e:
            logger.error("异步获取所有TODO失败: %s", e)
            raise

    async def health_check(self) -> bool:
        """健康检查."""
        return await self.db_ops.health_check()
