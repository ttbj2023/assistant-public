"""通用数据库操作组件 - 提供可复用的异步数据库操作

基于组合模式设计的通用数据库操作类,替代过度设计的继承架构.
提供常用CRUD操作,事务管理和通用过滤功能.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, TypeVar

from sqlalchemy import Select, delete, insert, select, update

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

T = TypeVar("T")

logger = logging.getLogger(__name__)


class AsyncDatabaseOperations[T]:
    """通用异步数据库操作类.

    提供基于组合模式的数据访问操作,替代继承架构.
    支持通用CRUD,批量操作和用户线程过滤.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        model_class: type[T],
    ) -> None:
        """初始化数据库操作组件.

        Args:
            session_factory: 数据库会话工厂
            model_class: 数据模型类

        """
        self.session_factory = session_factory
        self.model_class = model_class
        self._model_name = model_class.__name__

    @asynccontextmanager
    async def transaction_scope(self) -> AsyncGenerator[AsyncSession, None]:
        """事务上下文管理器."""
        async with self.session_factory() as session, session.begin():
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    async def create_with_validation(
        self,
        required_fields: list[str] | None = None,
        default_fields: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> T:
        """带字段验证的创建操作.

        Args:
            required_fields: 必需字段列表
            default_fields: 默认字段值
            **kwargs: 创建参数

        Returns:
            创建的记录实例

        Raises:
            ValueError: 缺少必需字段时

        """
        # 验证必需字段
        if required_fields:
            missing_fields = [
                field
                for field in required_fields
                if field not in kwargs or kwargs[field] is None
            ]
            if missing_fields:
                raise ValueError(f"缺少必需字段: {', '.join(missing_fields)}")

        # 合并默认值
        if default_fields:
            for field, default_value in default_fields.items():
                if field not in kwargs:
                    kwargs[field] = default_value

        return await self.create(**kwargs)

    async def create(self, **kwargs: Any) -> T:
        """创建记录.

        Args:
            **kwargs: 创建参数

        Returns:
            创建的记录实例

        """
        async with self.transaction_scope() as session:
            stmt = insert(self.model_class).returning(self.model_class)
            result = await session.execute(stmt, [kwargs])
            return result.scalar_one()

    async def get_by_id(self, item_id: int) -> T | None:
        """根据ID获取记录."""
        async with self.session_factory() as session:
            stmt = select(self.model_class).where(self.model_class.id == item_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_by_user_thread(self, user_id: str, thread_id: str) -> list[T]:
        """根据用户和线程ID获取记录."""
        async with self.session_factory() as session:
            stmt = select(self.model_class).where(
                self.model_class.user_id == user_id,
                self.model_class.thread_id == thread_id,
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, item_id: int, update_data: dict[str, Any]) -> T | None:
        """更新记录."""
        async with self.transaction_scope() as session:
            stmt = (
                update(self.model_class)
                .where(self.model_class.id == item_id)
                .values(**update_data)
                .returning(self.model_class)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def delete_by_id(self, item_id: int) -> bool:
        """根据ID删除记录."""
        async with self.transaction_scope() as session:
            stmt = delete(self.model_class).where(self.model_class.id == item_id)
            result = await session.execute(stmt)
            return result.rowcount > 0

    async def list_all(self, limit: int = 100, offset: int = 0) -> list[T]:
        """列出所有记录."""
        async with self.session_factory() as session:
            stmt = select(self.model_class).limit(limit).offset(offset)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def bulk_create(
        self,
        items: list[dict[str, Any]],
        required_fields: list[str] | None = None,
        default_fields: dict[str, Any] | None = None,
    ) -> list[T]:
        """批量创建记录.

        Args:
            items: 创建数据列表
            required_fields: 必需字段列表
            default_fields: 默认字段值

        Returns:
            创建的记录列表

        Raises:
            ValueError: 缺少必需字段时

        """
        if not items:
            return []

        # 验证和处理每个项目
        processed_items = []
        for item in items:
            # 验证必需字段
            if required_fields:
                missing_fields = [
                    field
                    for field in required_fields
                    if field not in item or item[field] is None
                ]
                if missing_fields:
                    raise ValueError(f"缺少必需字段: {', '.join(missing_fields)}")

            # 合并默认值
            if default_fields:
                for field, default_value in default_fields.items():
                    if field not in item:
                        item[field] = default_value

            processed_items.append(item)

        async with self.transaction_scope() as session:
            stmt = insert(self.model_class).returning(self.model_class)
            result = await session.execute(stmt, processed_items)
            return list(result.scalars().all())

    async def bulk_delete_by_user_thread(self, user_id: str, thread_id: str) -> int:
        """批量删除用户和线程的所有记录.

        Args:
            user_id: 用户ID
            thread_id: 线程ID

        Returns:
            删除的记录数量

        """
        async with self.transaction_scope() as session:
            stmt = delete(self.model_class).where(
                self.model_class.user_id == user_id,
                self.model_class.thread_id == thread_id,
            )
            result = await session.execute(stmt)
            return result.rowcount

    async def find_by_filters(
        self,
        filters: dict[str, Any],
        limit: int = 100,
        offset: int = 0,
    ) -> list[T]:
        """根据过滤条件查找记录."""
        async with self.session_factory() as session:
            stmt = select(self.model_class)

            # 应用过滤条件 (支持标量 == 和列表 in_())
            for field, value in filters.items():
                if hasattr(self.model_class, field) and value is not None:
                    column = getattr(self.model_class, field)
                    if isinstance(value, (list, tuple)):
                        if value:
                            stmt = stmt.where(column.in_(value))
                    else:
                        stmt = stmt.where(column == value)

            stmt = stmt.limit(limit).offset(offset)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    def apply_user_thread_filters(
        self,
        stmt: Select[tuple[T]],
        user_id: str | None = None,
        thread_id: str | None = None,
    ) -> Select[tuple[T]]:
        """应用用户和线程过滤条件.

        Args:
            stmt: SQL语句对象
            user_id: 用户ID
            thread_id: 线程ID

        Returns:
            应用过滤后的SQL语句

        """
        if user_id is not None:
            stmt = stmt.where(self.model_class.user_id == user_id)
        if thread_id is not None:
            stmt = stmt.where(self.model_class.thread_id == thread_id)

        return stmt

    async def health_check(self) -> bool:
        """健康检查."""
        try:
            async with self.session_factory() as session:
                # 尝试执行简单的查询
                await session.execute(select(self.model_class).limit(1))
                return True
        except Exception as e:
            logger.error(f"{self._model_name} health check failed: {e}")
            return False

    async def get_latest(
        self,
        user_id: str | None = None,
        thread_id: str | None = None,
        order_field: str = "created_at",
        limit: int = 1,
    ) -> list[T]:
        """获取最新记录."""
        async with self.session_factory() as session:
            stmt = select(self.model_class)

            # 应用用户线程过滤
            stmt = self.apply_user_thread_filters(stmt, user_id, thread_id)

            # 应用排序
            if hasattr(self.model_class, order_field):
                order_attr = getattr(self.model_class, order_field)
                stmt = stmt.order_by(order_attr.desc())

            stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())
