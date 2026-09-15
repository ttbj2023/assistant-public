"""日历单向 push 同步纯函数 (字段换算 + diff 计划).

无 IO: 引擎负责取数与执行, 本模块只做确定性换算与计划生成.
语义: 本地是唯一权威, 远端为只读镜像 —— 远端被编辑时用本地内容 revert,
远端被删除时重新 POST; 容器内非映射条目跳过不动 (用户手建数据).
重复事件 (recurrence_rule 非空) 暂不同步 (设计既定).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from src.storage.models.calendar_event import CalendarEventStatus
from src.sync.todo_sync import SyncAction, parse_graph_datetime

if TYPE_CHECKING:
    from src.storage.models.calendar_event import CalendarEvent


def _wall_clock(dt: datetime, tz: str) -> str:
    """aware UTC 时间转用户时区挂钟串 (Graph dateTime 格式)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%dT%H:%M:%S")


def local_event_to_graph_payload(event: CalendarEvent, *, tz: str) -> dict:
    """CalendarEvent -> Graph event 请求体 (创建/更新共用).

    全天事件: 本地 end 为 inclusive 结束日 → Graph isAllDay 语义为
    start 当天 00:00 / end 次日 00:00 (用户时区, 与 ics_builder 同构).
    """
    if event.all_day:
        zone = ZoneInfo(tz)
        start_local = event.start_time.astimezone(zone).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        # inclusive 结束日; start==end 表示当天事件
        end_base = (
            event.end_time if event.end_time > event.start_time else event.start_time
        )
        end_local = end_base.astimezone(zone).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ) + timedelta(days=1)
        start_s = start_local.strftime("%Y-%m-%dT%H:%M:%S")
        end_s = end_local.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        start_s = _wall_clock(event.start_time, tz)
        end_s = _wall_clock(event.end_time, tz)

    payload: dict = {
        "subject": event.title,
        "start": {"dateTime": start_s, "timeZone": tz},
        "end": {"dateTime": end_s, "timeZone": tz},
        "isAllDay": event.all_day,
    }
    if event.description:
        payload["body"] = {"contentType": "text", "content": event.description}
    if event.location:
        payload["location"] = {"displayName": event.location}
    return payload


def calendar_content_hash(event: CalendarEvent) -> str:
    """业务字段摘要 (不含 id/时间戳/重复规则), 用于"本地是否已改"判定."""
    parts = "|".join([
        event.title,
        event.description or "",
        event.location or "",
        _wall_clock(event.start_time, "UTC"),
        _wall_clock(event.end_time, "UTC"),
        str(event.all_day),
    ])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CalendarSyncPlan:
    """一轮日历同步的动作集合.

    actions: 引擎执行的动作; foreign_remote_ids: 容器内非映射远端条目
    (用户手建数据, 跳过不动, 引擎告警); recurring_skipped_local_ids:
    变为重复事件的已映射本地条目 (暂不同步, 引擎告警).
    """

    actions: list[SyncAction] = field(default_factory=list)
    foreign_remote_ids: list[str] = field(default_factory=list)
    recurring_skipped_local_ids: list[int] = field(default_factory=list)


def compute_calendar_sync_plan(
    local_events: list[CalendarEvent],
    remote_events: list[dict[str, Any]],
    mappings: list[Any],
    *,
    tz: str,
) -> CalendarSyncPlan:
    """计算一轮单向 push 同步计划.

    Args:
        local_events: 本地全部事件 (含各状态)
        remote_events: 专用子日历内全部 Graph event
        mappings: 已有 SyncMap 映射
        tz: 用户时区 (IANA, 请求体换算用)

    Returns:
        动作集合; 空动作 = 镜像一致

    """
    local_by_id = {e.id: e for e in local_events if e.id is not None}
    remote_by_id = {str(e.get("id")): e for e in remote_events}
    actions: list[SyncAction] = []
    recurring: list[int] = []

    mapped_local_ids: set[int] = set()
    mapped_remote_ids: set[str] = set()

    for mapping in mappings:
        local = local_by_id.get(mapping.local_id)
        remote = remote_by_id.get(str(mapping.remote_id))
        mapped_local_ids.add(mapping.local_id)
        mapped_remote_ids.add(str(mapping.remote_id))

        if local is None:
            # 本地已删 (硬删除) -> 远端删除
            actions.append(
                SyncAction(kind="push_delete", remote_id=str(mapping.remote_id)),
            )
            continue
        if local.status == CalendarEventStatus.CANCELLED:
            # 本地软取消 -> 远端删除
            actions.append(
                SyncAction(
                    kind="push_delete",
                    local_id=mapping.local_id,
                    remote_id=str(mapping.remote_id),
                ),
            )
            continue
        if local.recurrence_rule:
            # 变为重复事件: 重复事件暂不同步, 保留远端现状并告警
            recurring.append(mapping.local_id)
            continue
        if remote is None:
            # 远端被删 -> 重新 POST 保持镜像 (映射换新 remote_id 由引擎处理)
            actions.append(
                SyncAction(
                    kind="push_create",
                    local_id=mapping.local_id,
                    payload=local_event_to_graph_payload(local, tz=tz),
                ),
            )
            continue

        local_hash = calendar_content_hash(local)
        local_changed = mapping.content_hash != local_hash

        remote_modified = parse_graph_datetime(
            str(remote.get("lastModifiedDateTime", "")),
        )
        last_synced = mapping.last_synced_at
        if last_synced is not None and last_synced.tzinfo is None:
            last_synced = last_synced.replace(tzinfo=UTC)
        remote_edited = (
            remote_modified is not None
            and last_synced is not None
            and remote_modified > last_synced
        )

        if local_changed or remote_edited:
            # 本地是唯一权威: 本地已改或远端被外部编辑, 均以本地内容覆盖 (revert)
            actions.append(
                SyncAction(
                    kind="push_update",
                    local_id=mapping.local_id,
                    remote_id=str(mapping.remote_id),
                    payload=local_event_to_graph_payload(local, tz=tz),
                ),
            )

    for event in local_events:
        if event.id is None or event.id in mapped_local_ids:
            continue
        # 未映射的软取消事件: 远端无镜像, 无事可做
        if event.status != CalendarEventStatus.ACTIVE:
            continue
        # 重复事件暂不同步
        if event.recurrence_rule:
            continue
        actions.append(
            SyncAction(
                kind="push_create",
                local_id=event.id,
                payload=local_event_to_graph_payload(event, tz=tz),
            ),
        )

    foreign = [rid for rid in remote_by_id if rid not in mapped_remote_ids]

    return CalendarSyncPlan(
        actions=actions,
        foreign_remote_ids=foreign,
        recurring_skipped_local_ids=recurring,
    )


__all__ = [
    "CalendarSyncPlan",
    "calendar_content_hash",
    "compute_calendar_sync_plan",
    "local_event_to_graph_payload",
]
