"""日历同步纯函数 (字段换算 + diff 计划; 双向: 添加/编辑拉回, 删除单向自愈).

无 IO: 引擎负责取数与执行, 本模块只做确定性换算与计划生成.
语义: 远端编辑 pull_update 拉回本地 (双方都改 lastModifiedTime 新者胜,
相等本地胜); 远端新建 pull_create 流入本地; 远端被删除重新 POST
(删除保持单向自愈); 重复事件 (recurrence_rule 非空) 暂不同步
(push/pull 边界对称).
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


def graph_event_to_local_fields(event: dict[str, Any], *, tz: str) -> dict[str, Any]:
    """Graph event -> 本地 CalendarEvent 写入字段 (pull_update/pull_create 用).

    local_event_to_graph_payload 的逆换算: 定时事件挂钟串按事件时区还原为
    aware UTC; 全天事件 Graph 的 exclusive 次日零点还原为本地 inclusive
    结束日 (与 ics_builder 同构语义). dateTime 携带偏移时偏移优先.
    """
    zone_name = str((event.get("start") or {}).get("timeZone") or tz)

    def _parse_wall_clock(part: Any) -> datetime | None:
        if not isinstance(part, dict):
            return None
        raw = str(part.get("dateTime") or "")
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            zone = (
                ZoneInfo("UTC") if zone_name.upper() == "UTC" else ZoneInfo(zone_name)
            )
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(UTC)

    start = _parse_wall_clock(event.get("start"))
    end = _parse_wall_clock(event.get("end"))
    all_day = bool(event.get("isAllDay"))
    if all_day and end is not None:
        # Graph exclusive 次日零点 -> 本地 inclusive 结束日
        end = end - timedelta(days=1)

    body = event.get("body") or {}
    location = event.get("location") or {}
    description = str(body.get("content") or "")
    location_name = str(location.get("displayName") or "")
    return {
        "title": str(event.get("subject") or ""),
        "description": description or None,
        "location": location_name or None,
        "start_time": start,
        "end_time": end,
        "all_day": all_day,
    }


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
    变为重复事件的已映射本地条目 (暂不同步, 引擎告警);
    recurring_skipped_remote_ids: 手机新建的重复系列 (暂不流入, 引擎告警).
    """

    actions: list[SyncAction] = field(default_factory=list)
    foreign_remote_ids: list[str] = field(default_factory=list)
    recurring_skipped_local_ids: list[int] = field(default_factory=list)
    recurring_skipped_remote_ids: list[str] = field(default_factory=list)


def compute_calendar_sync_plan(
    local_events: list[CalendarEvent],
    remote_events: list[dict[str, Any]],
    mappings: list[Any],
    *,
    tz: str,
) -> CalendarSyncPlan:
    """计算一轮日历同步计划.

    Args:
        local_events: 本地全部事件 (含各状态)
        remote_events: 专用子日历内全部 Graph event
        mappings: 已有 SyncMap 映射
        tz: 用户时区 (IANA, 请求体换算用)

    Returns:
        动作集合; 空动作 = 双侧一致

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

        if local_changed and remote_edited:
            # 双方都改: lastModifiedTime 新者胜, 相等本地胜 (与 TODO 同构)
            local_updated = local.updated_at
            if local_updated is not None and local_updated.tzinfo is None:
                local_updated = local_updated.replace(tzinfo=UTC)
            local_newer = (
                local_updated is not None
                and remote_modified is not None
                and local_updated >= remote_modified
            )
            if local_newer:
                actions.append(
                    SyncAction(
                        kind="push_update",
                        local_id=mapping.local_id,
                        remote_id=str(mapping.remote_id),
                        payload=local_event_to_graph_payload(local, tz=tz),
                    ),
                )
            else:
                actions.append(
                    SyncAction(
                        kind="pull_update",
                        local_id=mapping.local_id,
                        remote_id=str(mapping.remote_id),
                        fields=graph_event_to_local_fields(remote, tz=tz),
                    ),
                )
        elif remote_edited:
            # 远端编辑且本地未改: 拉回本地 (双向; 原 revert 语义已翻转)
            actions.append(
                SyncAction(
                    kind="pull_update",
                    local_id=mapping.local_id,
                    remote_id=str(mapping.remote_id),
                    fields=graph_event_to_local_fields(remote, tz=tz),
                ),
            )
        elif local_changed:
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

    # 未映射远端事件 (手机在专用日历直接新建) → 拉入本地;
    # 重复系列暂不流入 (RRULE 转换器缺失, 与 push 侧边界对称)
    recurring_remote: list[str] = []
    for rid, event in remote_by_id.items():
        if rid in mapped_remote_ids:
            continue
        if event.get("recurrence") or event.get("seriesMasterId"):
            recurring_remote.append(rid)
            continue
        actions.append(
            SyncAction(
                kind="pull_create",
                remote_id=rid,
                fields=graph_event_to_local_fields(event, tz=tz),
            ),
        )

    return CalendarSyncPlan(
        actions=actions,
        foreign_remote_ids=[],
        recurring_skipped_local_ids=recurring,
        recurring_skipped_remote_ids=recurring_remote,
    )


__all__ = [
    "CalendarSyncPlan",
    "calendar_content_hash",
    "compute_calendar_sync_plan",
    "local_event_to_graph_payload",
]
