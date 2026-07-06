"""TODO业务服务.

提供TODO相关的业务逻辑封装,包括TODO创建,状态管理,优先级处理等功能.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, override

from src.storage.dao.async_todo_dao import AsyncTodoDAO
from src.storage.formatters.todo_formatter import create_todo_formatter
from src.storage.models.todo import TodoItem, TodoPriority, TodoStatus

from .health_check_mixin import ServiceHealthCheckMixin


class TodoService(ServiceHealthCheckMixin):
    """TODO业务服务.

    负责TODO相关的业务逻辑:
    - TODO创建和验证
    - 状态转换规则
    - 优先级管理
    - 截止日期提醒
    - 业务规则验证

    采用组合模式,使用通用功能组件.
    """

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        """初始化TODO服务.

        Args:
            session_factory: SQLAlchemy异步会话工厂

        """
        super().__init__()
        self.session_factory = session_factory
        self.logger = logging.getLogger(f"{__name__}.TodoService")

        # 组合DAO
        self.todo_dao = AsyncTodoDAO(session_factory)
        self.todo_formatter = create_todo_formatter()

    async def list_todos(
        self,
        user_id: str,
        thread_id: str | None = None,
        *,
        status: TodoStatus | None = None,
        statuses: list[TodoStatus] | None = None,
        priority: TodoPriority | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TodoItem]:
        """列出TODO项.

        Args:
            user_id: 用户ID
            thread_id: 线程ID,用于数据隔离
            status: 单个状态过滤
            statuses: 多状态过滤列表, 优先级高于 status
            priority: 优先级过滤
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            TODO项列表, 按 priority 降序排列 (urgent > high > medium > low)

        Raises:
             RuntimeError: 数据库操作失败

        """
        start_time = time.time()

        try:
            self.logger.info(
                "📋 开始获取TODO列表 - user_id: %s, thread_id: %s, status: %s, statuses: %s, priority: %s",
                user_id,
                thread_id,
                status,
                statuses,
                priority,
            )

            # 构建过滤条件 (statuses 优先于 status)
            filters: dict[str, Any] = {"user_id": user_id}
            if thread_id is not None:
                filters["thread_id"] = thread_id
            if statuses is not None:
                filters["status"] = statuses
            elif status is not None:
                filters["status"] = status
            if priority is not None:
                filters["priority"] = priority

            todos = await self.todo_dao.list_by_filters(
                limit=limit,
                offset=offset,
                **filters,
            )

            # 按 priority 降序排列: urgent > high > medium > low
            priority_order = {
                TodoPriority.URGENT: 0,
                TodoPriority.HIGH: 1,
                TodoPriority.MEDIUM: 2,
                TodoPriority.LOW: 3,
            }
            todos.sort(key=lambda t: priority_order.get(t.priority, 99))

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 获取TODO列表完成 - 找到{len(todos)}个TODO, duration: {duration:.2f}ms",
            )
            return todos

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 获取TODO列表失败 - duration: {duration:.2f}ms, user_id: {user_id}, error: {e}",
                exc_info=True,
            )
            raise RuntimeError(f"获取TODO列表失败: {e}") from e

    async def create_todo(
        self,
        title: str,
        user_id: str,
        thread_id: str,
        description: str = "",
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
            status: 初始状态
            due_date: 截止日期

        Returns:
            创建的TODO项

        Raises:
             ValueError: 输入验证失败
             RuntimeError: 数据库操作失败

        """
        start_time = time.time()

        try:
            self.logger.info(
                f"开始创建TODO - user_id: {user_id}, thread_id: {thread_id}, title: {title[:50]}..., priority: {priority}",
            )

            # 业务验证
            if not title.strip():
                raise ValueError("TODO标题不能为空")

            if len(title) > 200:
                raise ValueError("TODO标题长度不能超过200字符")

            if description and len(description) > 1000:
                raise ValueError("TODO描述长度不能超过1000字符")

            # 创建TODO项
            todo = TodoItem(
                title=title.strip(),
                description=description.strip(),
                user_id=user_id,
                thread_id=thread_id,
                status=status,
                priority=priority,
                due_date=due_date,
            )

            created_todo = await self.todo_dao.create_todo(
                title=todo.title,
                user_id=todo.user_id,
                thread_id=todo.thread_id,
                description=todo.description,
                priority=todo.priority,
                status=todo.status,
                due_date=todo.due_date,
            )

            # 验证返回的对象并记录信息,处理可能的DetachedInstanceError
            try:
                todo_id = created_todo.id if hasattr(created_todo, "id") else None
                todo_title = (
                    created_todo.title if hasattr(created_todo, "title") else todo.title
                )

                if todo_id:
                    self.logger.info(
                        "✅ TODO创建成功 - ID: %s, title: %s",
                        todo_id,
                        todo_title,
                    )
                else:
                    self.logger.warning("⚠️ TODO创建成功但无法获取ID信息")
            except Exception as log_error:
                # 记录日志错误但不影响主要流程
                self.logger.warning("⚠️ TODO创建成功但日志记录失败: %s", log_error)

            duration = (time.time() - start_time) * 1000
            self.logger.info(f"✅ 创建TODO完成 - duration: {duration:.2f}ms")
            return created_todo

        except ValueError:
            raise
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 创建TODO失败 - duration: {duration:.2f}ms, user_id: {user_id}, error: {e}",
                exc_info=True,
            )
            raise RuntimeError(f"创建TODO失败: {e}") from e

    async def update_todo(
        self,
        todo_id: int,
        user_id: str,
        title: str | None = None,
        description: str | None = None,
        status: TodoStatus | None = None,
        priority: TodoPriority | None = None,
        due_date: datetime | None = None,
    ) -> TodoItem | None:
        """更新TODO项.

        Args:
            todo_id: TODO ID
            user_id: 用户ID
            title: 新标题
            description: 新描述
            status: 新状态
            priority: 新优先级
            due_date: 新截止日期

        Returns:
            更新后的TODO项

        Raises:
             FileNotFoundError: TODO项不存在
             ValueError: 输入验证失败
             RuntimeError: 数据库操作失败

        """
        start_time = time.time()

        try:
            self.logger.info("✏️ 开始更新TODO - ID: %s, user_id: %s", todo_id, user_id)

            # 验证输入
            if title is not None:
                if not title.strip():
                    raise ValueError("TODO标题不能为空")
                if len(title) > 200:
                    raise ValueError("TODO标题长度不能超过200字符")

            if description is not None and len(description) > 1000:
                raise ValueError("TODO描述长度不能超过1000字符")

            # 构建更新数据
            update_data: dict[str, Any] = {}
            if title is not None:
                update_data["title"] = title.strip()
            if description is not None:
                update_data["description"] = description.strip()
            if status is not None:
                update_data["status"] = status
            if priority is not None:
                update_data["priority"] = priority
            if due_date is not None:
                update_data["due_date"] = due_date

            if not update_data:
                self.logger.info("无更新字段 - ID: %s", todo_id)
                duration = (time.time() - start_time) * 1000
                self.logger.info(
                    f"✅ 更新TODO完成 - 无更新字段, duration: {duration:.2f}ms",
                )

                # 获取现有TODO返回
                existing_todo = await self.todo_dao.get_todo_by_id(todo_id)
                if not existing_todo or existing_todo.user_id != user_id:
                    raise FileNotFoundError(f"TODO项不存在或无权限访问: {todo_id}")
                return existing_todo

            # 检查TODO是否存在且属于该用户
            existing_todo = await self.todo_dao.get_todo_by_id(todo_id)
            if not existing_todo or existing_todo.user_id != user_id:
                raise FileNotFoundError(f"TODO项不存在或无权限访问: {todo_id}")

            # 更新TODO
            updated_todo = await self.todo_dao.update_todo(todo_id, **update_data)

            # 处理可能的DetachedInstanceError
            try:
                self.logger.info(
                    f"✅ TODO更新成功 - ID: {updated_todo.id if updated_todo else 'unknown'}",
                )
            except Exception as log_error:
                self.logger.warning("⚠️ TODO更新成功但日志记录失败: %s", log_error)

            duration = (time.time() - start_time) * 1000
            self.logger.info(f"✅ 更新TODO完成 - 成功, duration: {duration:.2f}ms")
            return updated_todo

        except (FileNotFoundError, ValueError):
            raise
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 更新TODO失败 - duration: {duration:.2f}ms, ID: {todo_id}, user_id: {user_id}, error: {e}",
                exc_info=True,
            )
            raise RuntimeError(f"更新TODO失败: {e}") from e

    async def delete_todo(
        self,
        todo_id: int,
        user_id: str,
    ) -> bool:
        """删除TODO项.

        Args:
            todo_id: TODO ID
            user_id: 用户ID

        Returns:
            是否删除成功

        Raises:
             RuntimeError: 数据库操作失败

        """
        start_time = time.time()

        try:
            self.logger.info("🗑️ 开始删除TODO - ID: %s, user_id: %s", todo_id, user_id)

            # 检查TODO是否存在且属于该用户
            existing_todo = await self.todo_dao.get_todo_by_id(todo_id)
            if not existing_todo or existing_todo.user_id != user_id:
                self.logger.warning(
                    "⚠️ TODO不存在或无权限 - ID: %s, user_id: %s",
                    todo_id,
                    user_id,
                )
                duration = (time.time() - start_time) * 1000
                self.logger.info(
                    f"✅ 删除TODO完成 - 失败, duration: {duration:.2f}ms",
                )
                return False

            # 删除TODO
            deleted = await self.todo_dao.delete_todo(todo_id)

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 删除TODO完成 - 成功: {deleted}, duration: {duration:.2f}ms",
            )
            return deleted

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 删除TODO失败 - duration: {duration:.2f}ms, ID: {todo_id}, user_id: {user_id}, error: {e}",
                exc_info=True,
            )
            raise RuntimeError(f"删除TODO失败: {e}") from e

    async def get_todo_by_id(self, todo_id: int, user_id: str) -> TodoItem:
        """根据ID获取TODO项.

        Args:
            todo_id: TODO ID
            user_id: 用户ID

        Returns:
            TODO项

        Raises:
             FileNotFoundError: TODO项不存在
             RuntimeError: 数据库操作失败

        """
        start_time = time.time()

        try:
            self.logger.info("🔍 开始获取TODO - ID: %s, user_id: %s", todo_id, user_id)

            todo = await self.todo_dao.get_todo_by_id(todo_id)

            if not todo or todo.user_id != user_id:
                duration = (time.time() - start_time) * 1000
                self.logger.info(
                    f"⚠️ TODO不存在或无权限 - ID: {todo_id}, duration: {duration:.2f}ms",
                )
                raise FileNotFoundError(f"TODO项不存在或无权限访问: {todo_id}")

            duration = (time.time() - start_time) * 1000
            self.logger.info(f"✅ 获取TODO完成 - 成功, duration: {duration:.2f}ms")
            return todo

        except FileNotFoundError:
            raise
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 获取TODO失败 - duration: {duration:.2f}ms, ID: {todo_id}, user_id: {user_id}, error: {e}",
                exc_info=True,
            )
            raise RuntimeError(f"获取TODO失败: {e}") from e

    async def format_todos(
        self,
        todos: list[TodoItem] | list[dict] | None = None,
        user_id: str = "",
        thread_id: str = "",
        *,
        limit: int = 50,
        status: TodoStatus | None = TodoStatus.PENDING,
        statuses: list[TodoStatus] | None = None,
        priority: TodoPriority | None = None,
        include_section_title: bool = False,
        format_template: str = "markdown",
    ) -> str:
        """统一格式化TODO列表.

        Args:
            todos: TODO项列表,可以是TodoItem对象或字典(如果提供,则忽略user_id等参数)
            user_id: 用户ID(当需要获取TODO时必需)
            thread_id: 线程ID(当需要获取TODO时必需)
            limit: 返回数量限制(当从数据库获取时使用)
            status: 单个状态过滤(当从数据库获取时使用)
            statuses: 多状态过滤列表, 优先级高于 status
            priority: 优先级过滤(当从数据库获取时使用)
            include_section_title: 是否包含节标题
            format_template: 格式模板类型

        Returns:
            格式化后的TODO字符串

        Raises:
             RuntimeError: 当数据库操作失败时

        """
        start_time = time.time()

        try:
            operation_desc = "格式化TODO列表"
            if todos:
                operation_desc += f" - 预提供TODO数: {len(todos)}"
            elif user_id:
                operation_desc += f" - 数据库获取: user_id={user_id}, status={status}, statuses={statuses}, priority={priority}"

            self.logger.info("📝 开始%s", operation_desc)

            # 如果没有提供TODO列表,则从数据库获取
            if todos is None:
                if not user_id:
                    raise ValueError("当todos为None时,必须提供user_id")

                db_todos = await self.list_todos(
                    user_id=user_id,
                    status=status,
                    statuses=statuses,
                    priority=priority,
                    limit=limit,
                )
                todos = db_todos

            # 转换为字典格式
            todo_dicts: list[dict] = []
            for todo in todos or []:
                if isinstance(todo, dict):
                    todo_dicts.append(todo)
                else:
                    todo_dicts.append(todo.to_dict())

            # 使用formatter进行格式化
            formatted = await self.todo_formatter.format_todolist(
                todo_dicts,
                include_section_title=include_section_title,
                format_template=format_template,
            )

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 格式化TODO列表完成 - 输出长度: {len(formatted)}, duration: {duration:.2f}ms",
            )
            return formatted

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 格式化TODO列表失败 - duration: {duration:.2f}ms, user_id: {user_id}, thread_id: {thread_id}, error: {e}",
                exc_info=True,
            )
            raise RuntimeError(f"格式化TODO列表失败: {e}") from e

    async def get_formatted_todolist(
        self,
        user_id: str,
        thread_id: str,
        *,
        limit: int = 50,
        status: TodoStatus | None = TodoStatus.PENDING,
        statuses: list[TodoStatus] | None = None,
        priority: TodoPriority | None = None,
        include_section_title: bool = False,
        format_template: str = "markdown",
    ) -> str:
        """获取格式化的TODO列表字符串(组合查询和格式化)

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            limit: 返回数量限制
            status: 单个状态过滤
            statuses: 多状态过滤列表, 优先级高于 status
            priority: 优先级过滤
            include_section_title: 是否包含标题
            format_template: 格式化模板

        Returns:
            格式化后的TODO字符串

        """
        start_time = time.time()

        try:
            self.logger.info(
                "📋 开始获取格式化TODO列表 - user_id: %s, thread_id: %s, status: %s, statuses: %s, priority: %s",
                user_id,
                thread_id,
                status,
                statuses,
                priority,
            )

            # 获取TODO列表
            todos = await self.list_todos(
                user_id=user_id,
                thread_id=thread_id,
                status=status,
                statuses=statuses,
                priority=priority,
                limit=limit,
            )

            # 格式化TODO列表
            formatted = await self.format_todos(
                todos=todos,
                user_id=user_id,
                thread_id=thread_id,
                include_section_title=include_section_title,
                format_template=format_template,
            )

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 获取格式化TODO列表完成 - 输出长度: {len(formatted)}, duration: {duration:.2f}ms",
            )
            return formatted

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 获取格式化TODO列表失败 - duration: {duration:.2f}ms, user_id: {user_id}, thread_id: {thread_id}, error: {e}",
                exc_info=True,
            )
            raise RuntimeError(f"获取格式化TODO列表失败: {e}") from e

    @override
    async def _check_service_health(self) -> dict[str, Any]:
        """检查TODO服务健康状态.

        Returns:
            包含健康状态信息的字典

        """
        try:
            # 测试数据库连接
            async with self.session_factory() as session:
                # 尝试执行一个简单的查询来测试数据库连接
                from sqlalchemy import text

                await session.execute(text("SELECT 1"))

            # 获取TODO统计信息
            stats = await self._get_todo_statistics()

            return {
                "status": "healthy",
                "database_connected": True,
                "statistics": self._build_statistics(
                    total_todos=stats.get("total_todos", 0),
                    pending_todos=stats.get("pending_todos", 0),
                    completed_todos=stats.get("completed_todos", 0),
                    overdue_todos=stats.get("overdue_todos", 0),
                    due_today_todos=stats.get("due_today_todos", 0),
                    by_priority=stats.get("by_priority", {}),
                    latest_todo_time=stats.get("latest_todo_time"),
                ),
                "error": None,
                "additional_info": {
                    "dao_accessible": True,
                    "formatter_accessible": self.todo_formatter is not None,
                    "priorities_supported": [p.value for p in TodoPriority],
                    "statuses_supported": [s.value for s in TodoStatus],
                },
            }

        except Exception as e:
            error_msg = f"TODO服务健康检查失败: {e}"
            self.logger.error("❌ %s", error_msg, exc_info=True)

            return {
                "status": "unhealthy" if "connection" in str(e).lower() else "degraded",
                "database_connected": False,
                "statistics": {},
                "error": str(e),
                "additional_info": {
                    "dao_accessible": False,
                    "formatter_accessible": self.todo_formatter is not None,
                    "priorities_supported": [p.value for p in TodoPriority],
                    "statuses_supported": [s.value for s in TodoStatus],
                },
            }

    async def _get_todo_statistics(self) -> dict[str, Any]:
        """获取TODO统计信息.

        Returns:
            包含TODO统计信息的字典

        """
        try:
            async with self.session_factory() as session:
                from sqlalchemy import text

                count_result = await session.execute(
                    text("SELECT COUNT(*) FROM todo_items"),
                )
                total_todos = count_result.scalar() or 0

                # 按状态统计 (使用枚举 .name 保证与存储值一致)
                pending_result = await session.execute(
                    text("SELECT COUNT(*) FROM todo_items WHERE status = :status"),
                    {"status": TodoStatus.PENDING.name},
                )
                pending_todos = pending_result.scalar() or 0

                completed_result = await session.execute(
                    text("SELECT COUNT(*) FROM todo_items WHERE status = :status"),
                    {"status": TodoStatus.COMPLETED.name},
                )
                completed_todos = completed_result.scalar() or 0

                # 获取过期TODO数
                overdue_result = await session.execute(
                    text(
                        "SELECT COUNT(*) FROM todo_items WHERE due_date < :now AND status != :status",
                    ),
                    {"now": datetime.now(UTC), "status": TodoStatus.COMPLETED.name},
                )
                overdue_todos = overdue_result.scalar() or 0

                # 获取今日到期TODO数
                today_start = datetime.now(UTC).replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                today_end = today_start.replace(
                    hour=23,
                    minute=59,
                    second=59,
                    microsecond=999999,
                )
                due_today_result = await session.execute(
                    text(
                        "SELECT COUNT(*) FROM todo_items WHERE due_date BETWEEN :start AND :end",
                    ),
                    {"start": today_start, "end": today_end},
                )
                due_today_todos = due_today_result.scalar() or 0

                # 按优先级统计
                priority_result = await session.execute(
                    text("SELECT priority, COUNT(*) FROM todo_items GROUP BY priority"),
                )
                by_priority = {row[0]: row[1] for row in priority_result.fetchall()}

                # 获取最新TODO时间
                latest_result = await session.execute(
                    text("SELECT MAX(updated_at) FROM todo_items"),
                )
                latest_time = latest_result.scalar()

                return {
                    "total_todos": total_todos,
                    "pending_todos": pending_todos,
                    "completed_todos": completed_todos,
                    "overdue_todos": overdue_todos,
                    "due_today_todos": due_today_todos,
                    "by_priority": by_priority,
                    "latest_todo_time": latest_time.isoformat()
                    if latest_time
                    else None,
                }

        except Exception as e:
            self.logger.warning("获取TODO统计信息失败: %s", e)
            return {
                "total_todos": 0,
                "pending_todos": 0,
                "completed_todos": 0,
                "overdue_todos": 0,
                "due_today_todos": 0,
                "by_priority": {},
                "latest_todo_time": None,
            }
