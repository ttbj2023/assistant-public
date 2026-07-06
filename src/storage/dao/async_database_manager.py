"""异步数据库管理器.

负责异步数据库连接管理,表创建,会话管理等基础数据库操作.
基于SQLAlchemy async模式实现,提供真正的异步数据库访问能力.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from src.core.path_resolver import (
    get_database_path,
    get_user_database_path,
    get_user_path_resolver,
)
from src.storage.models.conversation import ConversationIndex, ConversationIndexGroup
from src.storage.models.file_registry import FileEntry
from src.storage.models.health_data import (
    DailyHealthSummary,
    ECGRecord,
    FoodProduct,
    MealRecord,
    MedicalReport,
    ShoppingItem,
    WeeklyHealthSummary,
    WeightRecord,
    WorkoutRecord,
    WorkoutSample,
)
from src.storage.models.price_alert import PriceAlertRule
from src.storage.models.scheduled_message import ScheduledMessage
from src.storage.models.simple_pinned_memory import SimplePinnedMemory
from src.storage.models.todo import TodoItem
from src.storage.models.usage import UsageRecord
from src.storage.models.user_channel_config import UserChannelConfig
from src.storage.models.user_requirement import UserRequirement

logger = logging.getLogger(__name__)

_DEFAULT_POOL_SIZE = 20
_DEFAULT_MAX_OVERFLOW = 30

_db_manager_cache: dict[str, AsyncDatabaseManager] = {}
_db_cache_lock = asyncio.Lock()


async def _get_or_create_db_manager(
    database_url: str,
    tables: list[type[SQLModel]] | None = None,
) -> AsyncDatabaseManager:
    cached = _db_manager_cache.get(database_url)
    if cached is not None:
        return cached
    async with _db_cache_lock:
        cached = _db_manager_cache.get(database_url)
        if cached is not None:
            return cached
        manager = AsyncDatabaseManager(database_url)
        if tables:
            await manager.create_tables(tables)
        _db_manager_cache[database_url] = manager
        return manager


async def close_all_db_managers() -> None:
    async with _db_cache_lock:
        for key, manager in _db_manager_cache.items():
            try:
                await manager.close()
            except Exception as e:
                logger.warning("关闭数据库管理器失败 %s: %s", key, e)
        _db_manager_cache.clear()
        logger.info("已关闭所有缓存的数据库管理器")


def _set_sqlite_pragma(dbapi_conn: Any, _connection_record: Any) -> None:
    """为SQLite连接设置优化的PRAGMA选项.

    配置说明:
    - journal_mode=WAL: 启用写前日志模式,支持1写+多读并发
    - foreign_keys=ON: 启用外键约束
    - synchronous=NORMAL: 平衡性能和安全性
    - cache_size: 增加缓存大小提升性能
    - wal_autocheckpoint=1000: 减少WAL文件自动检查点频率
    - locking_mode=NORMAL: 正常锁定模式,支持多进程并发

    Args:
        dbapi_conn: DBAPI连接对象
        connection_record: 连接记录

    """
    cursor = dbapi_conn.cursor()

    # 启用WAL模式(Write-Ahead Logging)- 支持并发读写
    cursor.execute("PRAGMA journal_mode=WAL")

    # 启用外键约束
    cursor.execute("PRAGMA foreign_keys=ON")

    # 设置同步模式为NORMAL(性能优化,WAL模式下安全)
    cursor.execute("PRAGMA synchronous=NORMAL")

    # 增加缓存大小(默认-2000,设置为10000提升性能)
    cursor.execute("PRAGMA cache_size=10000")

    # 设置临时存储在内存中
    cursor.execute("PRAGMA temp_store=MEMORY")

    # 设置WAL自动检查点(减少WAL文件大小)
    cursor.execute("PRAGMA wal_autocheckpoint=1000")

    # 正常锁定模式(多进程并发必须)
    cursor.execute("PRAGMA locking_mode=NORMAL")

    # 设置忙超时为5秒(并发冲突时等待时间)
    cursor.execute("PRAGMA busy_timeout=5000")

    cursor.close()


class AsyncDatabaseManager:
    """异步数据库管理器."""

    def __init__(self, database_url: str) -> None:
        """初始化异步数据库管理器."""
        # 转换为异步连接字符串
        if database_url == ":memory:":
            self.database_url = "sqlite+aiosqlite:///:memory:"
        elif database_url.startswith("sqlite:///"):
            self.database_url = database_url.replace(
                "sqlite:///",
                "sqlite+aiosqlite:///",
            )
        else:
            self.database_url = database_url

        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def engine(self) -> AsyncEngine:
        """获取异步数据库引擎."""
        if self._engine is None:
            # 确保数据库目录存在
            self._ensure_database_directory()

            # 记录数据库路径(用于调试并发问题)
            logger.debug(f"🔧 创建数据库引擎: {self.database_url}")

            # 内存数据库不支持连接池参数
            if self.database_url == "sqlite+aiosqlite:///:memory:":
                self._engine = create_async_engine(
                    self.database_url,
                    echo=False,
                )
            else:
                self._engine = create_async_engine(
                    self.database_url,
                    echo=False,
                    pool_size=_DEFAULT_POOL_SIZE,
                    max_overflow=_DEFAULT_MAX_OVERFLOW,
                    pool_timeout=30,
                    pool_recycle=3600,
                    connect_args={"check_same_thread": False},
                )

                # 为SQLite连接配置WAL模式和其他优化选项
                # 只对SQLite数据库应用PRAGMA设置
                if self.database_url.startswith("sqlite+aiosqlite:///"):

                    @event.listens_for(self._engine.sync_engine, "connect")
                    def on_connect(dbapi_conn: Any, connection_record: Any) -> None:
                        """新连接建立时设置PRAGMA选项"""
                        _set_sqlite_pragma(dbapi_conn, connection_record)

                    logger.debug("✅ 已为SQLite配置WAL模式和PRAGMA优化")

            logger.debug("✅ 数据库引擎创建成功")
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """获取异步会话工厂."""
        if self._session_factory is None:
            self._session_factory = async_sessionmaker(
                self.engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        return self._session_factory

    async def create_tables(self, tables: list[type[SQLModel]] | None = None) -> None:
        """异步创建指定的表,如果未指定则创建所有已注册的表.

        Args:
            tables: 要创建的表模型列表,如果为None则创建所有已注册的表

        """
        async with self.engine.begin() as conn:
            if tables:
                # 只创建指定的表
                for table_model in tables:
                    # 使用表模型的metadata.registry来创建单个表
                    if hasattr(table_model, "__table__"):
                        await conn.run_sync(
                            table_model.__table__.create,
                            checkfirst=True,
                        )
                        logger.debug(f"✅ 创建表: {table_model.__tablename__}")
                    else:
                        logger.warning("⚠️ 跳过无效的表模型: %s", table_model)
            else:
                # 创建所有已注册的表(向后兼容)
                await conn.run_sync(SQLModel.metadata.create_all, checkfirst=True)
        logger.info(
            f"✅ 异步数据库表创建完成 - 创建了 {len(tables) if tables else '全部'} 个表",
        )

    async def health_check(self) -> bool:
        """异步数据库健康检查."""
        try:
            async with self.session_factory() as session:
                await session.execute(text("SELECT 1"))
                return True
        except Exception as e:
            logger.error("异步数据库健康检查失败: %s", e)
            return False

    async def close(self) -> None:
        """关闭数据库连接."""
        if self._engine:
            await self._engine.dispose()
            logger.info("✅ 异步数据库连接已关闭")

    async def __aenter__(self) -> AsyncDatabaseManager:
        """异步上下文管理器入口."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val_: BaseException | None,
        exc_tb_: Any,
    ) -> None:
        """异步上下文管理器出口."""
        await self.close()

    def _ensure_database_directory(self) -> None:
        """确保数据库文件的父目录存在.

        修改:委托给path_resolver处理,避免重复逻辑.
        """
        if not self.database_url.startswith("sqlite+aiosqlite:///"):
            return

        match = re.match(r"sqlite\+aiosqlite:///(.+)", self.database_url)
        if not match:
            return

        db_path = Path(match.group(1))

        try:
            path_parts = db_path.parts
            if len(path_parts) >= 3 and path_parts[-2] == "database":
                db_path.parent.mkdir(parents=True, exist_ok=True)
                logger.debug("✅ 创建用户级数据库目录: %s", db_path.parent)
                return

            # 新路径结构: base_path/user_id/thread_id/agent_id/database/*.db
            if len(path_parts) >= 5:
                user_id = path_parts[-5]
                thread_id = path_parts[-4]
                agent_id = path_parts[-3]

                resolver = get_user_path_resolver()
                resolver.get_database_path(
                    user_id,
                    thread_id,
                    Path(db_path).stem,
                    agent_id=agent_id,
                )

                logger.debug(
                    "✅ 通过path_resolver创建数据库目录: %s/%s/%s",
                    user_id,
                    thread_id,
                    agent_id,
                )
            else:
                db_dir = db_path.parent
                try:
                    db_dir.mkdir(parents=True, exist_ok=True)
                    logger.info("✅ 创建数据库目录: %s", db_dir)
                except OSError as create_error:
                    if db_dir.exists():
                        logger.info("✅ 数据库目录已存在(并发创建): %s", db_dir)
                    else:
                        logger.error(
                            "❌ 创建数据库目录失败: %s, 错误: %s",
                            db_dir,
                            create_error,
                        )
                        raise

        except Exception as e:
            logger.warning("⚠️ 无法通过path_resolver创建目录,使用回退方案: %s", e)
            db_dir = db_path.parent
            try:
                db_dir.mkdir(parents=True, exist_ok=True)
                logger.info("✅ 创建数据库目录: %s", db_dir)
            except OSError as create_error:
                if db_dir.exists():
                    logger.info("✅ 数据库目录已存在(并发创建): %s", db_dir)
                else:
                    logger.error(
                        "❌ 创建数据库目录失败: %s, 错误: %s",
                        db_dir,
                        create_error,
                    )
                    raise


async def create_async_todo_db_manager(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
) -> AsyncDatabaseManager:
    """创建异步TODO数据库管理器实例 (Agent物理隔离, 全局缓存复用Engine).

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        异步TODO数据库管理器实例

    """
    db_path = get_database_path(user_id, thread_id, "todo", agent_id=agent_id)
    return await _get_or_create_db_manager(
        f"sqlite+aiosqlite:///{db_path}",
        tables=[TodoItem],
    )


async def create_async_pinned_memory_db_manager(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
) -> AsyncDatabaseManager:
    """创建异步置顶记忆数据库管理器实例 (Agent物理隔离, 全局缓存复用Engine).

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        异步置顶记忆数据库管理器实例

    """
    db_path = get_database_path(user_id, thread_id, "pinned_memory", agent_id=agent_id)
    database_url = f"sqlite+aiosqlite:///{db_path}"

    if database_url in _db_manager_cache:
        return _db_manager_cache[database_url]

    async with _db_cache_lock:
        if database_url in _db_manager_cache:
            return _db_manager_cache[database_url]

        manager = AsyncDatabaseManager(database_url)
        await manager.create_tables([SimplePinnedMemory])

        # 数据迁移: 清理已移除的 OTHER_INFO 枚举值 (f59c162e 重命名为 ADDRESSING)
        async with manager.engine.begin() as conn:
            result = await conn.execute(
                text(
                    "DELETE FROM simple_pinned_memory WHERE memory_type = 'OTHER_INFO'"
                ),
            )
            if result.rowcount > 0:
                logger.info("✅ 迁移: 清理 %d 条 OTHER_INFO 记忆", result.rowcount)

        _db_manager_cache[database_url] = manager
        return manager


async def create_async_requirement_memory_db_manager(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
) -> AsyncDatabaseManager:
    """创建异步用户要求记事本数据库管理器 (Agent物理隔离, 独立分库).

    与置顶记忆分库 (requirement_memory.db), 便于排除/删库调试.

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        异步数据库管理器实例

    """
    db_path = get_database_path(
        user_id,
        thread_id,
        "requirement_memory",
        agent_id=agent_id,
    )
    database_url = f"sqlite+aiosqlite:///{db_path}"

    if database_url in _db_manager_cache:
        return _db_manager_cache[database_url]

    async with _db_cache_lock:
        if database_url in _db_manager_cache:
            return _db_manager_cache[database_url]

        manager = AsyncDatabaseManager(database_url)
        await manager.create_tables([UserRequirement])

        _db_manager_cache[database_url] = manager
        return manager


async def create_async_conversation_history_db_manager(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
) -> AsyncDatabaseManager:
    """创建异步对话历史数据库管理器实例 (Agent物理隔离, 全局缓存复用Engine).

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        异步对话历史数据库管理器实例

    """
    db_path = get_database_path(
        user_id,
        thread_id,
        "conversation_history",
        agent_id=agent_id,
    )
    return await _get_or_create_db_manager(
        f"sqlite+aiosqlite:///{db_path}",
        tables=[ConversationIndex, ConversationIndexGroup],
    )


async def create_async_usage_db_manager(user_id: str) -> AsyncDatabaseManager:
    """创建异步用量统计数据库管理器实例 (用户级共享, 全局缓存复用Engine).

    Args:
        user_id: 用户ID

    Returns:
        异步用量统计数据库管理器实例

    """
    db_path = get_user_database_path(user_id, "usage")
    return await _get_or_create_db_manager(
        f"sqlite+aiosqlite:///{db_path}",
        tables=[UsageRecord],
    )


async def create_async_health_data_db_manager(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
) -> AsyncDatabaseManager:
    """创建异步健康数据数据库管理器实例 (Agent物理隔离, 全局缓存复用Engine).

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        异步健康数据数据库管理器实例

    Note:
        创建所有健康数据相关的表:
        - medical_reports: 体检报告
        - daily_health_summary: 每日健康汇总(含体重/活动/体征/睡眠)
        - weekly_health_summary: 每周健康趋势
        - shopping_items: 购物清单商品
        - workout_records: 运动记录
        - meal_records: 摄入记录
        - weight_records: 体重记录(原始, 允许同一天多条)

    """
    db_path = get_database_path(user_id, thread_id, "health_data", agent_id=agent_id)
    database_url = f"sqlite+aiosqlite:///{db_path}"

    if database_url in _db_manager_cache:
        return _db_manager_cache[database_url]

    async with _db_cache_lock:
        if database_url in _db_manager_cache:
            return _db_manager_cache[database_url]

        manager = AsyncDatabaseManager(database_url)

        health_tables = [
            MedicalReport,
            DailyHealthSummary,
            WeeklyHealthSummary,
            ShoppingItem,
            FoodProduct,
            WorkoutRecord,
            MealRecord,
            WeightRecord,
            WorkoutSample,
            ECGRecord,
        ]

        await manager.create_tables(health_tables)
        logger.info(f"✅ 创建健康数据数据库表: {len(health_tables)}个表")

        migration_columns = {
            "meal_records": [
                ("source", "VARCHAR DEFAULT 'conversation_extraction'"),
            ],
        }

        async with manager.engine.begin() as conn:
            for table_name, columns in migration_columns.items():
                for col_name, col_type in columns:
                    try:
                        await conn.execute(
                            text(
                                f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}",
                            ),
                        )
                        logger.info("✅ 迁移: %s.%s", table_name, col_name)
                    except Exception as e:
                        if "duplicate column name" in str(e).lower():
                            logger.debug("列已存在, 跳过: %s.%s", table_name, col_name)
                        else:
                            logger.warning(
                                "迁移失败 %s.%s: %s", table_name, col_name, e
                            )

        _db_manager_cache[database_url] = manager
        return manager


async def create_async_scheduled_message_db_manager(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
) -> AsyncDatabaseManager:
    """创建异步定时消息数据库管理器实例 (Agent物理隔离, 全局缓存复用Engine).

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        异步定时消息数据库管理器实例

    """
    db_path = get_database_path(
        user_id,
        thread_id,
        "scheduled_message",
        agent_id=agent_id,
    )
    return await _get_or_create_db_manager(
        f"sqlite+aiosqlite:///{db_path}",
        tables=[ScheduledMessage],
    )


async def create_async_channel_config_db_manager(
    user_id: str,
    thread_id: str,
    agent_id: str,
) -> AsyncDatabaseManager:
    """创建异步渠道配置数据库管理器实例 (Agent级, 全局缓存复用Engine).

    渠道配置按 (user, thread, agent) 物理隔离, 与 scheduled_message 同级.

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        异步渠道配置数据库管理器实例

    """
    db_path = get_database_path(
        user_id,
        thread_id,
        "channel_config",
        agent_id=agent_id,
    )
    return await _get_or_create_db_manager(
        f"sqlite+aiosqlite:///{db_path}",
        tables=[UserChannelConfig],
    )


async def create_async_price_alert_db_manager(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
) -> AsyncDatabaseManager:
    """创建异步价格监控数据库管理器实例 (Agent物理隔离, 全局缓存复用Engine).

    价格监控规则按 (user, thread, agent) 物理隔离, 与 scheduled_message /
    channel_config 同级. 一次性语义: 触发即结束.

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        异步价格监控数据库管理器实例

    """
    db_path = get_database_path(
        user_id,
        thread_id,
        "price_alert",
        agent_id=agent_id,
    )
    return await _get_or_create_db_manager(
        f"sqlite+aiosqlite:///{db_path}",
        tables=[PriceAlertRule],
    )


async def create_async_file_registry_db_manager(
    user_id: str,
) -> AsyncDatabaseManager:
    """创建用户级文件注册表数据库管理器实例 (用户级, 全局缓存复用 Engine).

    统一文件元数据与去重关联为单一 SSOT, 消除旧双轨架构 (file_hash_index +
    attachment_registry) 的跨库清理协同问题. 存储在
    data/{user_id}/database/file_registry.db.

    Args:
        user_id: 用户ID

    Returns:
        异步文件注册表数据库管理器实例

    """
    db_path = get_user_database_path(user_id, "file_registry")
    return await _get_or_create_db_manager(
        f"sqlite+aiosqlite:///{db_path}",
        tables=[FileEntry],
    )


# 移除了未使用的向后兼容同步工厂函数
# 原因:这些函数完全没有被使用,统一使用异步接口
# - create_async_todo_db_manager_sync()
# - create_async_pinned_memory_db_manager_sync()


__all__ = [
    "AsyncDatabaseManager",
    "close_all_db_managers",
    "create_async_channel_config_db_manager",
    "create_async_conversation_history_db_manager",
    "create_async_file_registry_db_manager",
    "create_async_health_data_db_manager",
    "create_async_pinned_memory_db_manager",
    "create_async_price_alert_db_manager",
    "create_async_scheduled_message_db_manager",
    "create_async_todo_db_manager",
    "create_async_usage_db_manager",
]
