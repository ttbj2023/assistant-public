"""TodoService 统计聚合集成测试.

验证 TodoService._get_todo_statistics 的 7 条手写 SQL 聚合逻辑, 取代单元测试中
把 SQL 执行顺序硬编码进 mock 的脚手架测试 (原 test_todo_service.py 的
TestTodoServiceGetTodoStatistics 类 — 手写 SessionWithStats + call_count 顺序索引).

测试策略: 灰盒 - 真实 TodoService + 真实 SQLite, 经 service.create_todo 预置各类
TODO (不同 status / priority / due_date), 验证统计结果反映真实聚合而非 mock 注入值.
仅 updated_at 由 DB 自动生成, 用 raw SQL 精确控制以验证 latest_todo_time 取最大值.

存储约定: SQLAlchemy Enum 列存枚举 .name (如 "PENDING"), 与 _get_todo_statistics
查询 (TodoStatus.PENDING.name) 自洽, 经真实 create_todo 路径写入无需手动干预.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from src.storage.models.todo import TodoPriority, TodoStatus
from src.storage.service.service_factory import create_todo_service

_AGENT_ID = "test-agent"


async def _set_updated_at(session_factory: object, todo_id: int, ts: datetime) -> None:
    """用 raw SQL 覆盖单条 todo 的 updated_at, 绕过 DB 自动时间戳."""
    async with session_factory() as session:  # type: ignore[call-arg]
        await session.execute(
            text("UPDATE todo_items SET updated_at = :ts WHERE id = :id"),
            {"ts": ts, "id": todo_id},
        )
        await session.commit()


@pytest.mark.integration
class TestTodoStatisticsIntegration:
    """TodoService._get_todo_statistics 真实 SQL 聚合验证."""

    @pytest.mark.asyncio
    async def test_statistics_empty_database_returns_zeros(
        self, test_user: str, test_thread_id: str
    ):
        """空库 (仅建表) 应返回全零统计与空优先级分布."""
        service = await create_todo_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )

        result = await service._get_todo_statistics()

        assert result["total_todos"] == 0
        assert result["pending_todos"] == 0
        assert result["completed_todos"] == 0
        assert result["overdue_todos"] == 0
        assert result["due_today_todos"] == 0
        assert result["by_priority"] == {}
        assert result["latest_todo_time"] is None

    @pytest.mark.asyncio
    async def test_statistics_aggregates_status_and_total_counts(
        self, test_user: str, test_thread_id: str
    ):
        """混合状态应正确统计 total / pending / completed 计数."""
        service = await create_todo_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )

        await service.create_todo(
            "t1", test_user, test_thread_id, status=TodoStatus.PENDING
        )
        await service.create_todo(
            "t2", test_user, test_thread_id, status=TodoStatus.PENDING
        )
        await service.create_todo(
            "t3", test_user, test_thread_id, status=TodoStatus.COMPLETED
        )
        await service.create_todo(
            "t4", test_user, test_thread_id, status=TodoStatus.IN_PROGRESS
        )

        result = await service._get_todo_statistics()

        assert result["total_todos"] == 4
        assert result["pending_todos"] == 2
        assert result["completed_todos"] == 1

    @pytest.mark.asyncio
    async def test_statistics_aggregates_overdue_and_due_today(
        self, test_user: str, test_thread_id: str
    ):
        """due_date + status 组合应正确判定 overdue 与 due_today.

        - PENDING + 昨日到期 → overdue (due_date < now 且 status != COMPLETED)
        - PENDING + 今日到期 → due_today
        - COMPLETED + 昨日到期 → 不算 overdue (status == COMPLETED 豁免)
        - PENDING + 明日到期 → 既非 overdue 也非 due_today
        """
        service = await create_todo_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )

        now = datetime.now(UTC)
        yesterday = now - timedelta(days=1)
        # 今日零点后 1 秒: 必然早于 now (任何时点跑均成立) 但仍属今日,
        # 专测 overdue(< today_start) 与 due_today 的边界互斥
        today_early = now.replace(hour=0, minute=0, second=1, microsecond=0)
        tomorrow = now + timedelta(days=1)

        await service.create_todo(
            "overdue_pending",
            test_user,
            test_thread_id,
            status=TodoStatus.PENDING,
            due_date=yesterday,
        )
        await service.create_todo(
            "due_today",
            test_user,
            test_thread_id,
            status=TodoStatus.PENDING,
            due_date=today_early,
        )
        await service.create_todo(
            "overdue_but_completed",
            test_user,
            test_thread_id,
            status=TodoStatus.COMPLETED,
            due_date=yesterday,
        )
        await service.create_todo(
            "future",
            test_user,
            test_thread_id,
            status=TodoStatus.PENDING,
            due_date=tomorrow,
        )

        result = await service._get_todo_statistics()

        assert result["overdue_todos"] == 1
        assert result["due_today_todos"] == 1

    @pytest.mark.asyncio
    async def test_statistics_aggregates_by_priority_and_latest_time(
        self, test_user: str, test_thread_id: str
    ):
        """按优先级 GROUP BY 应返回 name→count, latest_todo_time 取最大 updated_at."""
        service = await create_todo_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )

        t1 = await service.create_todo(
            "h1", test_user, test_thread_id, priority=TodoPriority.HIGH
        )
        await service.create_todo(
            "h2", test_user, test_thread_id, priority=TodoPriority.HIGH
        )
        await service.create_todo(
            "m1", test_user, test_thread_id, priority=TodoPriority.MEDIUM
        )
        await service.create_todo(
            "u1", test_user, test_thread_id, priority=TodoPriority.URGENT
        )

        # 远未来时间, 确保一定是所有 updated_at 中最大
        future = datetime(2099, 12, 31, 0, 0, 0, tzinfo=UTC)
        await _set_updated_at(service.session_factory, t1.id, future)

        result = await service._get_todo_statistics()

        assert result["by_priority"] == {"HIGH": 2, "MEDIUM": 1, "URGENT": 1}
        assert isinstance(result["latest_todo_time"], str)
        assert result["latest_todo_time"].startswith("2099-12-31")
