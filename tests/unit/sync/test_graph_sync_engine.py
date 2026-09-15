"""GraphSyncEngine 单元测试.

Mock 策略: Graph 层用可编程 FakeClient (记录调用, 按路由返回应答);
数据层 Mock DAO (AsyncMock, patch 引擎的 DAO getter);
验证计划执行 → Graph 调用与映射维护行为.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.sync.graph_sync_engine import GraphSyncEngine


def _write_token(base: Path, user_id: str) -> None:
    d = base / user_id / "credentials"
    d.mkdir(parents=True)
    (d / "graph_msa_tokens.json").write_text(
        json.dumps({"access_token": "at", "refresh_token": "rt"}),
    )


class FakeClient:
    """可编程 Graph 客户端替身: 按路由函数应答并记录调用."""

    def __init__(self, tokens=None, *, http=None, on_tokens_updated=None):
        self.tokens = tokens or {}
        self.calls: list[tuple[str, str]] = []
        self.router = lambda method, path: (200, {})

    async def request(self, method: str, path: str, **kwargs: Any):
        self.calls.append((method, path))
        return self.router(method, path)


class TestDiscover:
    """已授权用户发现测试."""

    def test_扫描credentials目录返回用户列表(self, tmp_path):
        _write_token(tmp_path, "u1")
        _write_token(tmp_path, "u2")
        (tmp_path / "u3").mkdir()  # 无 token 的用户不出现

        engine = GraphSyncEngine(base_path=tmp_path)

        assert engine.discover_authorized_users() == ["u1", "u2"]

    def test_无已授权用户返回空(self, tmp_path):
        engine = GraphSyncEngine(base_path=tmp_path)

        assert engine.discover_authorized_users() == []


def _sync_kind_todo():
    from src.storage.models.sync_map import SyncItemKind

    return SyncItemKind.TODO


def _sync_kind_event():
    from src.storage.models.sync_map import SyncItemKind

    return SyncItemKind.EVENT


def _local_todo(todo_id: int = 1, title: str = "买猫粮"):
    from src.storage.models.todo import TodoItem, TodoPriority, TodoStatus

    return TodoItem(
        id=todo_id,
        title=title,
        status=TodoStatus.PENDING,
        priority=TodoPriority.MEDIUM,
        user_id="u1",
        thread_id="t1",
    )


@contextmanager
def _mock_daos(engine, *, todos=None, mappings=None):
    """注入 Mock DAO getter; yield 各 Mock 便于断言."""
    todo_dao = AsyncMock()
    todo_dao.list_by_filters = AsyncMock(return_value=todos or [])
    map_dao = AsyncMock()
    map_dao.list_by_kind = AsyncMock(return_value=mappings or [])
    setting_dao = AsyncMock()
    setting_dao.get_value = AsyncMock(return_value=None)
    cal_dao = AsyncMock()
    cal_dao.list_active = AsyncMock(return_value=[])
    with (
        patch.object(engine, "_get_todo_dao", AsyncMock(return_value=todo_dao)),
        patch.object(engine, "_get_map_dao", AsyncMock(return_value=map_dao)),
        patch.object(engine, "_get_setting_dao", AsyncMock(return_value=setting_dao)),
        patch.object(engine, "_get_calendar_dao", AsyncMock(return_value=cal_dao)),
    ):
        yield todo_dao, map_dao, setting_dao, cal_dao


class TestDAOGetters:
    """DAO getter 真实构造测试 (db manager 签名回归防护, 不 mock)."""

    @pytest.mark.asyncio
    async def test_todo_dao_用户级管理器构造(self):
        from src.storage.dao.async_todo_dao import AsyncTodoDAO

        engine = GraphSyncEngine()

        dao = await engine._get_todo_dao("dao_check_user")

        assert isinstance(dao, AsyncTodoDAO)

    @pytest.mark.asyncio
    async def test_calendar_dao_用户级管理器构造(self):
        from src.storage.dao.async_calendar_event_dao import (
            AsyncCalendarEventDAO,
        )

        engine = GraphSyncEngine()

        dao = await engine._get_calendar_dao("dao_check_user")

        assert isinstance(dao, AsyncCalendarEventDAO)

    @pytest.mark.asyncio
    async def test_map与setting_dao_构造(self):
        from src.storage.dao.async_sync_map_dao import AsyncSyncMapDAO
        from src.storage.dao.async_sync_setting_dao import AsyncSyncSettingDAO

        engine = GraphSyncEngine()

        assert isinstance(
            await engine._get_map_dao("dao_check_user"), AsyncSyncMapDAO,
        )
        assert isinstance(
            await engine._get_setting_dao("dao_check_user"), AsyncSyncSettingDAO,
        )


class TestSyncUser:
    """sync_user 行为测试 (TODO + 日历动作执行)."""

    @pytest.mark.asyncio
    async def test_未授权用户_noop成功(self, tmp_path):
        engine = GraphSyncEngine(base_path=tmp_path)

        outcome = await engine.sync_user("nobody")

        assert outcome.ok is True
        assert outcome.todo_actions == 0

    @pytest.mark.asyncio
    async def test_新本地todo_推送创建并建映射(self, tmp_path):
        from src.storage.models.sync_map import SyncItemKind

        _write_token(tmp_path, "u1")
        engine = GraphSyncEngine(base_path=tmp_path)
        todo = _local_todo()
        fake = FakeClient()
        fake.router = lambda m, p: (
            (201, {"id": "rid-new"})
            if m == "POST" and "/tasks" in p
            else (200, {"value": []})
        )
        with (
            _mock_daos(engine, todos=[todo]) as (_, map_dao, __, ___),
            patch.object(engine, "_make_client", lambda *a: fake),
            patch.object(
                engine,
                "_ensure_todo_container",
                AsyncMock(return_value="lid-1"),
            ),
            patch.object(
                engine,
                "_ensure_calendar_container",
                AsyncMock(return_value="cal-1"),
            ),
        ):
            outcome = await engine.sync_user("u1")

        assert outcome.ok is True
        assert outcome.todo_actions == 1
        # POST 打到专用清单 tasks 端点
        assert ("POST", "/me/todo/lists/lid-1/tasks") in fake.calls
        # 映射建立: local 1 -> rid-new
        kwargs = map_dao.upsert.call_args.kwargs
        assert kwargs["local_id"] == 1
        assert kwargs["remote_id"] == "rid-new"
        assert kwargs["kind"] == SyncItemKind.TODO
        assert kwargs["content_hash"]  # 带内容摘要
        assert kwargs["last_synced_at"]  # 带同步时间

    @pytest.mark.asyncio
    async def test_远端新任务_拉取创建本地并建映射(self, tmp_path):
        from src.storage.models.todo import TodoItem

        _write_token(tmp_path, "u1")
        engine = GraphSyncEngine(base_path=tmp_path)
        fake = FakeClient()
        fake.router = lambda m, p: (
            (
                200,
                {
                    "value": [
                        {
                            "id": "rid-remote",
                            "title": "手机上建的",
                            "status": "notStarted",
                            "importance": "normal",
                            "lastModifiedDateTime": "2026-09-15T08:00:00.0000000Z",
                        },
                    ],
                },
            )
            if m == "GET" and "/tasks" in p
            else (200, {"value": []})
        )
        created = TodoItem(
            id=42,
            title="手机上建的",
            user_id="u1",
            thread_id="graph_sync",
        )
        with (
            _mock_daos(engine) as (todo_dao, map_dao, __, ___),
            patch.object(engine, "_make_client", lambda *a: fake),
            patch.object(
                engine,
                "_ensure_todo_container",
                AsyncMock(return_value="lid-1"),
            ),
            patch.object(
                engine,
                "_ensure_calendar_container",
                AsyncMock(return_value="cal-1"),
            ),
        ):
            todo_dao.create_todo = AsyncMock(return_value=created)
            outcome = await engine.sync_user("u1")

        assert outcome.ok is True
        # 本地创建带溯源 thread_id=graph_sync
        create_kwargs = todo_dao.create_todo.call_args.kwargs
        assert create_kwargs["title"] == "手机上建的"
        assert create_kwargs["thread_id"] == "graph_sync"
        # 映射: local 42 -> rid-remote
        kwargs = map_dao.upsert.call_args.kwargs
        assert kwargs["local_id"] == 42
        assert kwargs["remote_id"] == "rid-remote"

    @pytest.mark.asyncio
    async def test_授权失效_暂停且outcome失败_重授权后恢复(self, tmp_path):
        from src.sync.msgraph_client import MSGraphAuthError

        _write_token(tmp_path, "u1")
        engine = GraphSyncEngine(base_path=tmp_path)
        fake = FakeClient()
        fake.router = lambda m, p: (_ for _ in ()).throw(
            MSGraphAuthError("token 刷新失败"),
        )
        with (
            _mock_daos(engine),
            patch.object(engine, "_make_client", lambda *a: fake),
        ):
            outcome = await engine.sync_user("u1")

            assert outcome.ok is False
            assert "auth" in outcome.error
            # 同一 token 文件 (mtime 未变) → tick 跳过
            assert engine._is_paused("u1") is True

            # 模拟重新授权: token 文件被替换 → 恢复
            import os

            os.utime(
                tmp_path / "u1" / "credentials" / "graph_msa_tokens.json",
                (2_000_000_000, 2_000_000_000),
            )
            assert engine._is_paused("u1") is False

    @pytest.mark.asyncio
    async def test_分页取数_跟随nextLink直到取完(self, tmp_path):
        _write_token(tmp_path, "u1")
        engine = GraphSyncEngine(base_path=tmp_path)
        fake = FakeClient()
        pages = {
            "/me/todo/lists/lid-1/tasks?$top=200": (
                200,
                {
                    "value": [{"id": "a"}],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/todo/lists/lid-1/tasks?$skiptoken=x",
                },
            ),
            "/me/todo/lists/lid-1/tasks?$skiptoken=x": (
                200,
                {"value": [{"id": "b"}]},
            ),
        }
        fake.router = lambda m, p: pages.get(p, (200, {"value": []}))

        items = await engine._fetch_paged(
            fake,
            "/me/todo/lists/lid-1/tasks?$top=200",
        )

        assert [i["id"] for i in items] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_新本地事件_推送日历并建映射(self, tmp_path):
        from datetime import UTC, datetime

        from src.storage.models.calendar_event import CalendarEvent
        from src.storage.models.sync_map import SyncItemKind

        _write_token(tmp_path, "u1")
        engine = GraphSyncEngine(base_path=tmp_path)
        event = CalendarEvent(
            id=5,
            title="牙医",
            start_time=datetime(2026, 9, 20, 2, 0, tzinfo=UTC),
            end_time=datetime(2026, 9, 20, 3, 0, tzinfo=UTC),
            user_id="u1",
        )
        fake = FakeClient()
        fake.router = lambda m, p: (
            (201, {"id": "rid-evt"})
            if m == "POST" and "/events" in p
            else (200, {"value": []})
        )
        with (
            _mock_daos(engine) as (_, map_dao, __, cal_dao),
            patch.object(engine, "_make_client", lambda *a: fake),
            patch.object(
                engine,
                "_ensure_todo_container",
                AsyncMock(return_value="lid-1"),
            ),
            patch.object(
                engine,
                "_ensure_calendar_container",
                AsyncMock(return_value="cal-1"),
            ),
        ):
            cal_dao.list_active = AsyncMock(return_value=[event])
            outcome = await engine.sync_user("u1")

        assert outcome.ok is True
        assert outcome.calendar_actions == 1
        assert ("POST", "/me/calendars/cal-1/events") in fake.calls
        kwargs = map_dao.upsert.call_args.kwargs
        assert kwargs["kind"] == SyncItemKind.EVENT
        assert kwargs["local_id"] == 5
        assert kwargs["remote_id"] == "rid-evt"

    @pytest.mark.asyncio
    async def test_tick_暂停用户被跳过(self, tmp_path):
        token = tmp_path / "u1" / "credentials" / "graph_msa_tokens.json"
        _write_token(tmp_path, "u1")
        engine = GraphSyncEngine(base_path=tmp_path)
        engine._auth_paused["u1"] = token.stat().st_mtime  # 与文件一致 = 暂停中
        with patch.object(engine, "sync_user", AsyncMock()) as mock_sync:
            await engine.tick()

        mock_sync.assert_not_awaited()
        assert engine.stats.last_users == 1  # 发现了但跳过


class TestWake:
    """写后即时信号测试 (只加速本地→远端方向)."""

    def test_wake记录用户且事件置位(self):
        engine = GraphSyncEngine()
        engine._task = object()  # 模拟已启动

        engine.wake("u1")

        assert engine.consume_wake_users() == {"u1"}

    def test_consume后清空_再次消费为空(self):
        engine = GraphSyncEngine()
        engine._task = object()  # 模拟已启动
        engine.wake("u1")

        assert engine.consume_wake_users() == {"u1"}
        assert engine.consume_wake_users() == set()
        assert engine._wake_event.is_set() is False

    def test_未启动时wake不生效(self):
        engine = GraphSyncEngine()
        engine._task = None

        engine.wake("u1")

        # 引擎未运行: 信号被丢弃, 不留积压
        assert engine._wake_event.is_set() is False


class TestPerUserOverrides:
    """per-user 容器名/时区覆盖测试 (graph_sync_settings 表)."""

    @pytest.mark.asyncio
    async def test_容器名读取per_user覆盖(self, tmp_path):
        _write_token(tmp_path, "u1")
        engine = GraphSyncEngine(base_path=tmp_path)
        fake = FakeClient()
        fake.router = lambda m, p: (200, {"value": []})
        setting_values = {"todo_list_name": "我的助手", "calendar_name": "家历"}

        async def get_value(user_id, key):
            return setting_values.get(key)

        with (
            _mock_daos(engine) as (_, __, setting_dao, ___),
            patch.object(engine, "_make_client", lambda *a: fake),
        ):
            setting_dao.get_value = AsyncMock(side_effect=get_value)
            with (
                patch(
                    "src.sync.graph_sync_engine.ensure_todo_list",
                    AsyncMock(return_value=("lid-x", False)),
                ) as mock_ensure_list,
                patch(
                    "src.sync.graph_sync_engine.ensure_calendar",
                    AsyncMock(return_value=("cal-x", False)),
                ) as mock_ensure_cal,
            ):
                await engine.sync_user("u1")

        assert mock_ensure_list.call_args.kwargs["name"] == "我的助手"
        assert mock_ensure_cal.call_args.kwargs["name"] == "家历"


class TestSyncPathsRealDAO:
    """引擎同步全路径集成测试 (真实 DAO + FakeClient, 不 mock 数据层).

    走完整 _sync_todo / _sync_calendar: 覆盖 db manager 签名、DAO 调用、
    字段在 plan 与执行器之间的传递 (防"双重解析/键名错位"类静默损坏).
    每用例独立 user_id, 落测试数据根目录 (conftest 重定向).
    """

    @staticmethod
    def _router_for(
        remote_tasks: list[dict] | None = None,
        remote_events: list[dict] | None = None,
    ):
        """构造 FakeClient 路由: GET 返回给定远端态, 写操作固定应答."""
        posts: dict[str, int] = {}

        def route(method: str, path: str):
            if "/tasks" in path:
                if method == "GET":
                    return 200, {"value": remote_tasks or []}
                if method == "POST":
                    n = posts["task"] = posts.get("task", 0) + 1
                    return 201, {"id": f"rid-task-{n}"}
                return 204, None
            if "/events" in path or "/me/events" in path:
                if method == "GET":
                    return 200, {"value": remote_events or []}
                if method == "POST":
                    n = posts["evt"] = posts.get("evt", 0) + 1
                    return 201, {"id": f"rid-evt-{n}"}
                return 204, None
            return 200, {"value": []}

        return route, posts

    async def _engine_with(
        self, user_id: str, route,
    ) -> tuple[GraphSyncEngine, FakeClient]:
        engine = GraphSyncEngine()
        fake = FakeClient()
        fake.router = route
        engine._make_client = lambda *a: fake  # noqa: SLF001
        engine._ensure_todo_container = AsyncMock(return_value="lid-real")  # noqa: SLF001
        engine._ensure_calendar_container = AsyncMock(return_value="cal-real")  # noqa: SLF001
        return engine, fake

    @pytest.mark.asyncio
    async def test_todo推送_真实库建映射_二轮收敛(self):
        route, posts = self._router_for()
        engine, fake = await self._engine_with("it_push", route)
        todo_dao = await engine._get_todo_dao("it_push")
        await todo_dao.create_todo(
            title="买猫粮", user_id="it_push", thread_id="t1",
        )

        n1 = await engine._sync_todo(fake, "it_push", "lid-real")

        assert n1 == 1  # 未映射本地条目 → push_create
        assert posts["task"] == 1
        map_dao = await engine._get_map_dao("it_push")
        mappings = await map_dao.list_by_kind("it_push", _sync_kind_todo())
        assert len(mappings) == 1
        assert mappings[0].local_id == 1
        assert mappings[0].remote_id == "rid-task-1"
        assert mappings[0].content_hash

        # 二轮: 远端回显同 id + lastModified 晚于映射时间 → 无新动作
        local = await todo_dao.list_by_filters(limit=100, user_id="it_push")
        remote_view = [{
            "id": "rid-task-1",
            "title": "买猫粮",
            "status": "notStarted",
            "importance": "normal",
            "lastModifiedDateTime": mappings[0].last_synced_at.strftime(
                "%Y-%m-%dT%H:%M:%S.0000000Z",
            ),
        }]
        route2, posts2 = self._router_for(remote_tasks=remote_view)
        engine2, fake2 = await self._engine_with("it_push", route2)
        n2 = await engine2._sync_todo(fake2, "it_push", "lid-real")
        assert n2 == 0
        assert "task" not in posts2

    @pytest.mark.asyncio
    async def test_todo拉取_字段完整落库不降级(self):
        # 远端含 dueDateTime/body/high importance/completed → 本地须原样
        remote = [{
            "id": "rid-r",
            "title": "手机上建的重要事",
            "status": "completed",
            "importance": "high",
            "body": {"content": "备注文字"},
            "dueDateTime": {"dateTime": "2026-09-30T00:00:00", "timeZone": "UTC"},
            "lastModifiedDateTime": "2026-09-15T08:00:00.0000000Z",
        }]
        route, _ = self._router_for(remote_tasks=remote)
        engine, fake = await self._engine_with("it_pull", route)

        n = await engine._sync_todo(fake, "it_pull", "lid-real")

        assert n == 1
        from src.storage.models.todo import TodoPriority, TodoStatus

        todo_dao = await engine._get_todo_dao("it_pull")
        local = await todo_dao.list_by_filters(limit=100, user_id="it_pull")
        assert len(local) == 1
        item = local[0]
        assert item.title == "手机上建的重要事"
        assert item.status == TodoStatus.COMPLETED  # 不降级为 PENDING
        # 有损映射: URGENT/HIGH 推送侧均 "high", 拉取侧 "high"→URGENT
        assert item.priority == TodoPriority.URGENT
        assert item.description == "备注文字"  # 不丢失
        assert item.due_date is not None  # 不丢失
        assert item.thread_id == "graph_sync"  # 溯源
        map_dao = await engine._get_map_dao("it_pull")
        mappings = await map_dao.list_by_kind("it_pull", _sync_kind_todo())
        assert mappings[0].remote_id == "rid-r"

    @pytest.mark.asyncio
    async def test_日历推送_真实库建映射(self):
        from datetime import UTC, datetime

        from src.storage.models.calendar_event import CalendarEvent

        route, posts = self._router_for()
        engine, fake = await self._engine_with("it_cal", route)
        cal_dao = await engine._get_calendar_dao("it_cal")
        await cal_dao.create_event(
            title="牙医",
            user_id="it_cal",
            start_time=datetime(2026, 9, 20, 2, 0, tzinfo=UTC),
            end_time=datetime(2026, 9, 20, 3, 0, tzinfo=UTC),
        )

        n = await engine._sync_calendar(fake, "it_cal", "cal-real", "Asia/Shanghai")

        assert n == 1
        assert posts["evt"] == 1
        assert ("POST", "/me/calendars/cal-real/events") in fake.calls
        map_dao = await engine._get_map_dao("it_cal")
        mappings = await map_dao.list_by_kind("it_cal", _sync_kind_event())
        assert len(mappings) == 1
        assert mappings[0].remote_id == "rid-evt-1"
        assert mappings[0].content_hash

    @pytest.mark.asyncio
    async def test_日历远端被编辑_revert回推本地内容(self):
        from datetime import UTC, datetime

        from src.storage.models.calendar_event import CalendarEvent

        # 第一轮: push 建立映射
        route, _ = self._router_for()
        engine, fake = await self._engine_with("it_rev", route)
        cal_dao = await engine._get_calendar_dao("it_rev")
        await cal_dao.create_event(
            title="原始标题",
            user_id="it_rev",
            start_time=datetime(2026, 9, 20, 2, 0, tzinfo=UTC),
            end_time=datetime(2026, 9, 20, 3, 0, tzinfo=UTC),
        )
        await engine._sync_calendar(fake, "it_rev", "cal-real", "Asia/Shanghai")

        # 第二轮: 远端 lastModified 晚于映射时间 = 被外部编辑 → revert
        map_dao = await engine._get_map_dao("it_rev")
        mappings = await map_dao.list_by_kind("it_rev", _sync_kind_event())
        remote_edited = [{
            "id": "rid-evt-1",
            "subject": "被改过的标题",
            "lastModifiedDateTime": "2026-09-16T08:00:00.0000000Z",
        }]
        route2, _ = self._router_for(remote_events=remote_edited)
        engine2, fake2 = await self._engine_with("it_rev", route2)
        n2 = await engine2._sync_calendar(
            fake2, "it_rev", "cal-real", "Asia/Shanghai",
        )

        assert n2 == 1
        patch_calls = [c for c in fake2.calls if c[0] == "PATCH"]
        assert len(patch_calls) == 1
        assert patch_calls[0][1] == "/me/events/rid-evt-1"
