"""Graph 同步引擎 (TODO 双向 + 日历双向, 删除单向自愈).

PriceAlertEngine 同款骨架: 全局单例, FastAPI lifespan 启停, 周期 tick +
per-user 写后即时信号 (wake). 每 tick 逐用户串行同步 (Graph 限流友好);
授权失效用户暂停并告警, token 文件被替换 (重新授权) 后自动恢复.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from src.core.datetime_utils import now_utc
from src.sync.calendar_sync import compute_calendar_sync_plan
from src.sync.graph_containers import ensure_calendar, ensure_todo_list
from src.sync.msgraph_client import GRAPH_BASE_URL, MSGraphAuthError, MSGraphClient
from src.sync.todo_sync import (
    compute_todo_sync_plan,
    todo_content_hash,
)
from src.sync.token_store import GraphTokenStore

if TYPE_CHECKING:
    from src.storage.dao.async_calendar_event_dao import AsyncCalendarEventDAO
    from src.storage.dao.async_sync_map_dao import AsyncSyncMapDAO
    from src.storage.dao.async_sync_setting_dao import AsyncSyncSettingDAO
    from src.storage.dao.async_todo_dao import AsyncTodoDAO

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 600.0
_DEFAULT_TODO_LIST_NAME = "Assistant"
_DEFAULT_CALENDAR_NAME = "Assistant"
_DEFAULT_TIMEZONE = "Asia/Shanghai"
_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_MAX_PAGE = 20
_LOCAL_FETCH_LIMIT = 5000


@dataclass
class UserSyncOutcome:
    """单用户一轮同步结果."""

    user_id: str
    ok: bool = True
    todo_actions: int = 0
    calendar_actions: int = 0
    error: str = ""


@dataclass
class EngineStats:
    """引擎运行统计."""

    running: bool = False
    total_ticks: int = 0
    last_tick_at: str = ""
    last_users: int = 0
    last_error: str = ""
    user_outcomes: dict[str, str] = field(default_factory=dict)


class GraphSyncEngine:
    """Graph 同步全局引擎 (单例)."""

    def __init__(
        self,
        *,
        interval_seconds: float = _DEFAULT_INTERVAL,
        todo_list_name: str = _DEFAULT_TODO_LIST_NAME,
        calendar_name: str = _DEFAULT_CALENDAR_NAME,
        default_timezone: str = _DEFAULT_TIMEZONE,
        http: httpx.AsyncClient | None = None,
        base_path: Path | None = None,
    ) -> None:
        self._interval = interval_seconds
        self._todo_list_name = todo_list_name
        self._calendar_name = calendar_name
        self._default_tz = default_timezone
        self._http = http
        self._base_path = base_path
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._wake_users: set[str] = set()
        # 授权暂停: user_id -> 失败时 token 文件 mtime (文件被替换即恢复)
        self._auth_paused: dict[str, float] = {}
        self._notified_paused: set[str] = set()
        self.stats = EngineStats()

    # ── 生命周期 ──────────────────────────────────────────

    def apply_config(
        self,
        *,
        interval_seconds: float | None = None,
        todo_list_name: str | None = None,
        calendar_name: str | None = None,
        default_timezone: str | None = None,
    ) -> None:
        """应用全局默认配置 (须在 start 之前调用)."""
        if self._task is not None:
            raise RuntimeError("引擎已运行, 不允许改配置")
        if interval_seconds is not None:
            self._interval = interval_seconds
        if todo_list_name is not None:
            self._todo_list_name = todo_list_name
        if calendar_name is not None:
            self._calendar_name = calendar_name
        if default_timezone is not None:
            self._default_tz = default_timezone

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self.stats.running = True
        self._task = asyncio.create_task(self._run(), name="graph-sync-engine")
        logger.info(
            "GraphSyncEngine 已启动 (interval=%.0fs)",
            self._interval,
        )

    async def stop(self) -> None:
        self.stats.running = False
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        logger.info("GraphSyncEngine 已停止")

    def wake(self, user_id: str) -> None:
        """本地写操作后投递即时信号 (只加速本地→远端方向).

        引擎未运行时静默丢弃, 不留积压.
        """
        if self._task is None:
            return
        self._wake_users.add(user_id)
        self._wake_event.set()

    def consume_wake_users(self) -> set[str]:
        """取走已唤醒的用户集合并复位信号."""
        users = set(self._wake_users)
        self._wake_users.clear()
        self._wake_event.clear()
        return users

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.tick()
            except Exception as e:
                logger.exception("tick 异常 (非致命, 继续): %s", e)
                self.stats.last_error = str(e)
            # 周期兜底; wake 信号提前唤醒 (只处理被唤醒用户)
            woken = False
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._interval,
                )
                woken = self._wake_event.is_set()
            if woken:
                for user_id in sorted(self.consume_wake_users()):
                    if self._is_paused(user_id):
                        continue
                    try:
                        await self.sync_user(user_id)
                    except Exception as e:
                        logger.exception(
                            "即时同步异常 user=%s: %s",
                            user_id,
                            e,
                        )

    # ── 发现与调度 ────────────────────────────────────────

    def discover_authorized_users(self) -> list[str]:
        """扫描 token 文件发现已授权用户 (同 discover_price_alert_dbs 模式)."""
        root = self._base_path if self._base_path is not None else _data_root()
        if not root.exists():
            return []
        users = [
            p.parent.parent.name
            for p in root.glob("*/credentials/graph_msa_tokens.json")
        ]
        return sorted(users)

    async def tick(self) -> None:
        """一轮同步: 逐用户串行."""
        self.stats.total_ticks += 1
        self.stats.last_tick_at = now_utc().isoformat()
        users = self.discover_authorized_users()
        self.stats.last_users = len(users)
        for user_id in users:
            if self._is_paused(user_id):
                continue
            outcome = await self.sync_user(user_id)
            self.stats.user_outcomes[user_id] = (
                "ok" if outcome.ok else f"error: {outcome.error}"
            )
            if not outcome.ok:
                logger.warning(
                    "同步失败 user=%s: %s",
                    user_id,
                    outcome.error,
                )

    async def sync_user(self, user_id: str) -> UserSyncOutcome:
        """同步单个用户 (TODO 双向 + 日历双向, 删除单向自愈)."""
        store = GraphTokenStore(user_id, base_path=self._base_path)
        tokens = store.load()
        if tokens is None:
            return UserSyncOutcome(user_id=user_id, ok=True)
        client = self._make_client(tokens, store)
        outcome = UserSyncOutcome(user_id=user_id)
        try:
            tz = await self._user_timezone(user_id)
            list_id = await self._ensure_todo_container(client, user_id)
            if list_id is not None:
                outcome.todo_actions = await self._sync_todo(
                    client,
                    user_id,
                    list_id,
                )
            cal_id = await self._ensure_calendar_container(client, user_id)
            if cal_id is not None:
                outcome.calendar_actions = await self._sync_calendar(
                    client,
                    user_id,
                    cal_id,
                    tz,
                )
        except MSGraphAuthError as e:
            self._pause_user(user_id)
            outcome.ok = False
            outcome.error = f"auth: {e}"
            await self._notify_auth_expired(user_id, str(e))
        except Exception as e:
            outcome.ok = False
            outcome.error = str(e)
        return outcome

    def _make_client(
        self,
        tokens: dict[str, Any],
        store: GraphTokenStore,
    ) -> MSGraphClient:
        """构造该用户的 Graph 客户端 (token 轮换回写 token 文件)."""
        return MSGraphClient(
            tokens,
            http=self._ensure_http(),
            on_tokens_updated=store.save,
        )

    # ── TODO 双向同步 ─────────────────────────────────────

    async def _sync_todo(
        self,
        client: MSGraphClient,
        user_id: str,
        list_id: str,
    ) -> int:
        todo_dao = await self._get_todo_dao(user_id)
        local = await todo_dao.list_by_filters(
            limit=_LOCAL_FETCH_LIMIT,
            user_id=user_id,
        )
        remote = await self._fetch_paged(
            client,
            f"/me/todo/lists/{list_id}/tasks?$top=200",
        )
        map_dao = await self._get_map_dao(user_id)
        mappings = await map_dao.list_by_kind(user_id, _todo_kind())
        plan = compute_todo_sync_plan(local, remote, mappings)
        local_by_id = {t.id: t for t in local if t.id is not None}
        for action in plan.actions:
            await self._apply_todo_action(
                client,
                user_id,
                list_id,
                action,
                local_by_id,
            )
        return len(plan.actions)

    async def _apply_todo_action(
        self,
        client: MSGraphClient,
        user_id: str,
        list_id: str,
        action: Any,
        local_by_id: dict[int, Any],
    ) -> None:
        map_dao = await self._get_map_dao(user_id)
        todo_dao = await self._get_todo_dao(user_id)
        kind = action.kind

        if kind == "push_create":
            status, data = await client.request(
                "POST",
                f"/me/todo/lists/{list_id}/tasks",
                json_body=action.payload,
            )
            if status == 201 and data and action.local_id is not None:
                local = local_by_id.get(action.local_id)
                await map_dao.upsert(
                    user_id=user_id,
                    kind=_todo_kind(),
                    local_id=action.local_id,
                    remote_id=str(data["id"]),
                    content_hash=(
                        todo_content_hash(local) if local is not None else None
                    ),
                    last_synced_at=now_utc(),
                )
            else:
                logger.warning(
                    "push_create 失败 HTTP %s: local=%s",
                    status,
                    action.local_id,
                )
        elif kind == "push_update":
            status, _ = await client.request(
                "PATCH",
                f"/me/todo/lists/{list_id}/tasks/{action.remote_id}",
                json_body=action.payload,
            )
            if status in (200, 204) and action.local_id is not None:
                local = local_by_id.get(action.local_id)
                await map_dao.upsert(
                    user_id=user_id,
                    kind=_todo_kind(),
                    local_id=action.local_id,
                    remote_id=str(action.remote_id),
                    content_hash=(
                        todo_content_hash(local) if local is not None else None
                    ),
                    last_synced_at=now_utc(),
                )
            else:
                logger.warning(
                    "push_update 失败 HTTP %s: local=%s",
                    status,
                    action.local_id,
                )
        elif kind == "push_delete":
            status, _ = await client.request(
                "DELETE",
                f"/me/todo/lists/{list_id}/tasks/{action.remote_id}",
            )
            if status in (200, 204):
                if action.local_id is not None:
                    await map_dao.delete_by_local(
                        user_id, _todo_kind(), action.local_id
                    )
                else:
                    await map_dao.delete_by_remote(
                        user_id,
                        _todo_kind(),
                        str(action.remote_id),
                    )
            else:
                logger.warning(
                    "push_delete 失败 HTTP %s: remote=%s",
                    status,
                    action.remote_id,
                )
        elif kind == "pull_create":
            # fields 已是本地形状 (plan 里经 graph_task_to_local_fields 解析),
            # 直接使用, 不得二次解析 (会丢 due_date/description 并降级枚举)
            fields = action.fields or {}
            created = await todo_dao.create_todo(
                title=str(fields.get("title", "")),
                user_id=user_id,
                thread_id="graph_sync",
                description=fields.get("description"),
                priority=fields.get("priority"),
                status=fields.get("status"),
                due_date=fields.get("due_date"),
            )
            if created.id is not None:
                await map_dao.upsert(
                    user_id=user_id,
                    kind=_todo_kind(),
                    local_id=created.id,
                    remote_id=str(action.remote_id),
                    content_hash=todo_content_hash(created),
                    last_synced_at=now_utc(),
                )
        elif kind == "pull_update":
            fields = action.fields or {}
            updated = await todo_dao.update_todo(action.local_id, **fields)
            if updated is not None and updated.id is not None:
                await map_dao.upsert(
                    user_id=user_id,
                    kind=_todo_kind(),
                    local_id=updated.id,
                    remote_id=str(action.remote_id),
                    content_hash=todo_content_hash(updated),
                    last_synced_at=now_utc(),
                )
        elif kind == "pull_delete":
            await todo_dao.delete_todo(action.local_id)
            await map_dao.delete_by_local(user_id, _todo_kind(), action.local_id)

    # ── 日历同步 (双向: 添加/编辑拉回, 删除单向自愈) ──────

    async def _sync_calendar(
        self,
        client: MSGraphClient,
        user_id: str,
        cal_id: str,
        tz: str,
    ) -> int:
        cal_dao = await self._get_calendar_dao(user_id)
        local = await cal_dao.list_active(user_id, limit=_LOCAL_FETCH_LIMIT)
        remote = await self._fetch_paged(
            client,
            f"/me/calendars/{cal_id}/events"
            f"?$top=100&$select=id,subject,start,end,isAllDay,body,"
            f"lastModifiedDateTime,location,recurrence,seriesMasterId",
        )
        map_dao = await self._get_map_dao(user_id)
        mappings = await map_dao.list_by_kind(user_id, _event_kind())
        plan = compute_calendar_sync_plan(local, remote, mappings, tz=tz)
        if plan.foreign_remote_ids:
            logger.warning(
                "专用日历内存在非映射条目 (%s 个), 跳过不动 (疑似用户手建): %s",
                len(plan.foreign_remote_ids),
                plan.foreign_remote_ids[:5],
            )
        if plan.recurring_skipped_local_ids:
            logger.warning(
                "已映射事件变为重复规则, 暂不同步保留远端现状: %s",
                plan.recurring_skipped_local_ids[:5],
            )
        if plan.recurring_skipped_remote_ids:
            logger.warning(
                "手机新建的重复系列暂不流入本地 (RRULE 转换未支持): %s",
                plan.recurring_skipped_remote_ids[:5],
            )
        local_by_id = {e.id: e for e in local if e.id is not None}
        for action in plan.actions:
            await self._apply_calendar_action(
                client,
                user_id,
                cal_id,
                action,
                local_by_id,
                tz,
            )
        return len(plan.actions)

    async def _apply_calendar_action(
        self,
        client: MSGraphClient,
        user_id: str,
        cal_id: str,
        action: Any,
        local_by_id: dict[int, Any],
        tz: str,
    ) -> None:
        from src.sync.calendar_sync import calendar_content_hash

        map_dao = await self._get_map_dao(user_id)
        kind = action.kind

        if kind == "push_create":
            status, data = await client.request(
                "POST",
                f"/me/calendars/{cal_id}/events",
                json_body=action.payload,
                prefer_timezone=tz,
            )
            if status == 201 and data and action.local_id is not None:
                local = local_by_id.get(action.local_id)
                await map_dao.upsert(
                    user_id=user_id,
                    kind=_event_kind(),
                    local_id=action.local_id,
                    remote_id=str(data["id"]),
                    content_hash=(
                        calendar_content_hash(local) if local is not None else None
                    ),
                    last_synced_at=now_utc(),
                )
            else:
                logger.warning(
                    "日历 push_create 失败 HTTP %s: local=%s",
                    status,
                    action.local_id,
                )
        elif kind == "push_update":
            status, _ = await client.request(
                "PATCH",
                f"/me/events/{action.remote_id}",
                json_body=action.payload,
                prefer_timezone=tz,
            )
            if status in (200, 204) and action.local_id is not None:
                local = local_by_id.get(action.local_id)
                await map_dao.upsert(
                    user_id=user_id,
                    kind=_event_kind(),
                    local_id=action.local_id,
                    remote_id=str(action.remote_id),
                    content_hash=(
                        calendar_content_hash(local) if local is not None else None
                    ),
                    last_synced_at=now_utc(),
                )
            else:
                logger.warning(
                    "日历 push_update 失败 HTTP %s: local=%s",
                    status,
                    action.local_id,
                )
        elif kind == "push_delete":
            status, _ = await client.request(
                "DELETE",
                f"/me/events/{action.remote_id}",
            )
            if status in (200, 204):
                if action.local_id is not None:
                    await map_dao.delete_by_local(
                        user_id,
                        _event_kind(),
                        action.local_id,
                    )
                else:
                    await map_dao.delete_by_remote(
                        user_id,
                        _event_kind(),
                        str(action.remote_id),
                    )
            else:
                logger.warning(
                    "日历 push_delete 失败 HTTP %s: remote=%s",
                    status,
                    action.remote_id,
                )
        elif kind == "pull_create":
            # 手机在专用日历直接新建 → 流入本地; 走 CalendarService 保持
            # 与工具/REST 同一入口 (溯源 thread_id="graph_sync")
            fields = dict(action.fields or {})
            start_time = fields.get("start_time")
            if start_time is None:
                logger.warning(
                    "日历 pull_create 缺 start_time, 跳过: remote=%s",
                    action.remote_id,
                )
                return
            from src.storage.service.service_factory import create_calendar_service

            cal_service = await create_calendar_service(user_id)
            created = await cal_service.create_event(
                title=str(fields.get("title") or "未命名事件"),
                start_time=start_time,
                end_time=fields.get("end_time") or start_time,
                description=fields.get("description"),
                location=fields.get("location"),
                all_day=bool(fields.get("all_day")),
                source_thread_id="graph_sync",
            )
            if created.id is not None:
                await map_dao.upsert(
                    user_id=user_id,
                    kind=_event_kind(),
                    local_id=created.id,
                    remote_id=str(action.remote_id),
                    content_hash=calendar_content_hash(created),
                    last_synced_at=now_utc(),
                )
        elif kind == "pull_update":
            # 手机编辑 (拖拽改期等) → 拉回本地; 必须走 CalendarService:
            # update_event 内含影子级联 (关联定时消息自动顺延), 直捣
            # DAO 会绕过级联
            from src.storage.service.service_factory import create_calendar_service

            cal_service = await create_calendar_service(user_id)
            fields = dict(action.fields or {})
            update_data = {
                k: v
                for k, v in fields.items()
                if v is not None or k in ("description", "location")
            }
            if "start_time" not in update_data:
                logger.warning(
                    "日历 pull_update 缺 start_time, 跳过: local=%s",
                    action.local_id,
                )
                return
            result = await cal_service.update_event(int(action.local_id), update_data)
            if result.event is not None:
                if result.cascade.rescheduled_message_ids:
                    logger.info(
                        "手机改期级联顺延影子: local=%s -> %s",
                        action.local_id,
                        result.cascade.rescheduled_message_ids,
                    )
                await map_dao.upsert(
                    user_id=user_id,
                    kind=_event_kind(),
                    local_id=action.local_id,
                    remote_id=str(action.remote_id),
                    content_hash=calendar_content_hash(result.event),
                    last_synced_at=now_utc(),
                )
            else:
                logger.warning(
                    "日历 pull_update 本地条目不存在: local=%s",
                    action.local_id,
                )

    # ── 远端取数 (分页) ───────────────────────────────────

    async def _fetch_paged(self, client: MSGraphClient, path: str) -> list[dict]:
        """跟随 @odata.nextLink 取全量 (上限 _MAX_PAGE 页)."""
        items: list[dict] = []
        url = path
        for _ in range(_MAX_PAGE):
            status, data = await client.request("GET", url)
            if status != 200 or not isinstance(data, dict):
                raise RuntimeError(f"Graph 分页取数失败 HTTP {status}: {path}")
            items.extend(data.get("value", []))
            next_link = data.get("@odata.nextLink")
            if not next_link:
                return items
            url = str(next_link).removeprefix(GRAPH_BASE_URL)
        logger.warning("分页超过 %d 页, 截断: %s", _MAX_PAGE, path)
        return items

    # ── 授权暂停与恢复 ────────────────────────────────────

    def _pause_user(self, user_id: str) -> None:
        store = GraphTokenStore(user_id, base_path=self._base_path)
        try:
            mtime = store.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if user_id not in self._auth_paused:
            logger.error(
                "授权失效, 同步暂停 (重新授权后自动恢复): user=%s",
                user_id,
            )
        self._auth_paused[user_id] = mtime
        self._notified_paused.discard(user_id)

    def _is_paused(self, user_id: str) -> bool:
        paused_at = self._auth_paused.get(user_id)
        if paused_at is None:
            return False
        store = GraphTokenStore(user_id, base_path=self._base_path)
        try:
            mtime = store.path.stat().st_mtime
        except OSError:
            return True
        if mtime != paused_at:
            # token 文件已替换 (重新授权) → 恢复
            self._auth_paused.pop(user_id, None)
            logger.info("检测到重新授权, 恢复同步: user=%s", user_id)
            return False
        return True

    async def _notify_auth_expired(self, user_id: str, error: str) -> None:
        """授权失效提醒: per-user 设置 notify_delivery (JSON) 存在才发, 否则仅日志."""
        if user_id in self._notified_paused:
            return
        self._notified_paused.add(user_id)
        try:
            setting_dao = await self._get_setting_dao(user_id)
            raw = await setting_dao.get_value(user_id, "notify_delivery")
        except Exception:
            raw = None
        if not raw:
            logger.warning(
                "用户 %s 授权失效未配置通知渠道 (notify_delivery), 仅日志告警: %s",
                user_id,
                error,
            )
            return
        from src.core.notification import DeliverySpec, get_notification_service

        try:
            spec = DeliverySpec(**json.loads(raw))
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                "notify_delivery 配置非法, 跳过通知: user=%s, %s", user_id, e
            )
            return
        outcome = await get_notification_service().send(
            spec,
            f"Assistant 的 Outlook 同步授权已失效, 请重新授权 (DELETE 后重新发起).\n{error}",
            subject="Graph 同步授权失效",
        )
        if not outcome.ok:
            logger.error("授权失效通知发送失败: user=%s, %s", user_id, outcome.error)

    # ── 协作者构造 (测试可注入) ──────────────────────────

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
        return self._http

    async def _user_timezone(self, user_id: str) -> str:
        setting_dao = await self._get_setting_dao(user_id)
        value = await setting_dao.get_value(user_id, "timezone")
        return value or self._default_tz

    async def _ensure_todo_container(
        self,
        client: MSGraphClient,
        user_id: str,
    ) -> str | None:
        setting_dao = await self._get_setting_dao(user_id)
        name = (
            await setting_dao.get_value(user_id, "todo_list_name")
            or self._todo_list_name
        )
        list_id, _ = await ensure_todo_list(client, name=name)
        await setting_dao.set_value(user_id, "todo_list_id", list_id)
        return list_id

    async def _ensure_calendar_container(
        self,
        client: MSGraphClient,
        user_id: str,
    ) -> str | None:
        setting_dao = await self._get_setting_dao(user_id)
        name = (
            await setting_dao.get_value(user_id, "calendar_name") or self._calendar_name
        )
        cal_id, _ = await ensure_calendar(client, name=name)
        await setting_dao.set_value(user_id, "calendar_id", cal_id)
        return cal_id

    async def _get_todo_dao(self, user_id: str) -> AsyncTodoDAO:
        from src.storage.dao.async_database_manager import (
            create_async_todo_db_manager,
        )
        from src.storage.dao.async_todo_dao import AsyncTodoDAO

        # todo.db 为用户级统一库 (data/{user_id}/database/), 只传 user_id
        db = await create_async_todo_db_manager(user_id)
        return AsyncTodoDAO(db.session_factory)

    async def _get_calendar_dao(self, user_id: str) -> AsyncCalendarEventDAO:
        from src.storage.dao.async_calendar_event_dao import AsyncCalendarEventDAO
        from src.storage.dao.async_database_manager import (
            create_async_calendar_db_manager,
        )

        db = await create_async_calendar_db_manager(user_id)
        return AsyncCalendarEventDAO(db.session_factory)

    async def _get_map_dao(self, user_id: str) -> AsyncSyncMapDAO:
        from src.storage.dao.async_database_manager import (
            create_async_graph_sync_db_manager,
        )
        from src.storage.dao.async_sync_map_dao import AsyncSyncMapDAO

        db = await create_async_graph_sync_db_manager(user_id)
        return AsyncSyncMapDAO(db.session_factory)

    async def _get_setting_dao(self, user_id: str) -> AsyncSyncSettingDAO:
        from src.storage.dao.async_database_manager import (
            create_async_graph_sync_db_manager,
        )
        from src.storage.dao.async_sync_setting_dao import AsyncSyncSettingDAO

        db = await create_async_graph_sync_db_manager(user_id)
        return AsyncSyncSettingDAO(db.session_factory)


def _todo_kind() -> Any:
    from src.storage.models.sync_map import SyncItemKind

    return SyncItemKind.TODO


def _event_kind() -> Any:
    from src.storage.models.sync_map import SyncItemKind

    return SyncItemKind.EVENT


def _data_root() -> Path:
    from src.core.path_resolver import get_user_path_resolver

    return get_user_path_resolver().base_path


# ───────────────────── 单例 + 生命周期 ─────────────────────

_engine_instance: GraphSyncEngine | None = None


def get_graph_sync_engine() -> GraphSyncEngine:
    """获取或创建 GraphSyncEngine 单例."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = GraphSyncEngine()
    return _engine_instance


async def shutdown_graph_sync_engine() -> None:
    """应用关闭时调用, 停止引擎并释放资源."""
    global _engine_instance
    if _engine_instance is not None:
        await _engine_instance.stop()
        _engine_instance = None


def notify_local_write(user_id: str) -> None:
    """本地 TODO/日历写操作后投递即时同步信号 (服务层统一钩子).

    引擎未启动时静默丢弃; 惰性 import 由调用方负责避免循环依赖.
    """
    get_graph_sync_engine().wake(user_id)


__all__ = [
    "EngineStats",
    "GraphSyncEngine",
    "UserSyncOutcome",
    "get_graph_sync_engine",
    "notify_local_write",
    "shutdown_graph_sync_engine",
]
