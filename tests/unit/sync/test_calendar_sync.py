"""calendar_sync 纯函数测试 (转换 + 单向 push 同步计划).

不依赖数据库/HTTP: 本地侧用 CalendarEvent 实例, 远端侧用 Graph event dict,
映射侧用 SyncMap 实例; 只验证 diff 逻辑与字段换算 (单向 push + 镜像修复).
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.storage.models.calendar_event import (
    CalendarEvent,
    CalendarEventStatus,
)
from src.storage.models.sync_map import SyncItemKind, SyncMap
from src.sync.calendar_sync import (
    calendar_content_hash,
    compute_calendar_sync_plan,
    local_event_to_graph_payload,
)

TZ = "Asia/Shanghai"


def _event(
    *,
    id: int = 1,
    title: str = "牙医",
    start: datetime | None = None,
    end: datetime | None = None,
    all_day: bool = False,
    description: str | None = None,
    location: str | None = None,
    recurrence_rule: str | None = None,
    status: CalendarEventStatus = CalendarEventStatus.ACTIVE,
) -> CalendarEvent:
    return CalendarEvent(
        id=id,
        title=title,
        description=description,
        location=location,
        start_time=start or datetime(2026, 9, 20, 2, 0, tzinfo=UTC),
        end_time=end or datetime(2026, 9, 20, 3, 0, tzinfo=UTC),
        all_day=all_day,
        recurrence_rule=recurrence_rule,
        status=status,
        user_id="test_user",
    )


def _remote(
    *,
    rid: str = "rid-1",
    subject: str = "牙医",
    last_modified: str = "2026-09-15T08:00:00.0000000Z",
) -> dict:
    return {
        "id": rid,
        "subject": subject,
        "lastModifiedDateTime": last_modified,
    }


def _mapping(
    *,
    local_id: int = 1,
    remote_id: str = "rid-1",
    content_hash: str | None = None,
    last_synced_at: datetime | None = None,
) -> SyncMap:
    return SyncMap(
        id=local_id,
        user_id="test_user",
        kind=SyncItemKind.EVENT,
        local_id=local_id,
        remote_id=remote_id,
        content_hash=content_hash,
        last_synced_at=last_synced_at or datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
    )


class TestLocalToGraphPayload:
    """本地事件 -> Graph 请求体换算测试."""

    def test_timed事件_转用户时区挂钟时间(self):
        event = _event(
            start=datetime(2026, 9, 20, 2, 0, tzinfo=UTC),  # 上海 10:00
            end=datetime(2026, 9, 20, 3, 0, tzinfo=UTC),  # 上海 11:00
        )

        payload = local_event_to_graph_payload(event, tz=TZ)

        assert payload["subject"] == "牙医"
        assert payload["start"] == {
            "dateTime": "2026-09-20T10:00:00",
            "timeZone": TZ,
        }
        assert payload["end"] == {
            "dateTime": "2026-09-20T11:00:00",
            "timeZone": TZ,
        }
        assert payload["isAllDay"] is False

    def test_全天事件_inclusive结束日转次日零点(self):
        event = _event(
            start=datetime(2026, 9, 20, 16, 0, tzinfo=UTC),  # 上海 9-21 00:00
            end=datetime(2026, 9, 22, 16, 0, tzinfo=UTC),  # inclusive 结束日 9-23
            all_day=True,
        )

        payload = local_event_to_graph_payload(event, tz=TZ)

        assert payload["isAllDay"] is True
        assert payload["start"] == {
            "dateTime": "2026-09-21T00:00:00",
            "timeZone": TZ,
        }
        assert payload["end"] == {
            "dateTime": "2026-09-24T00:00:00",  # inclusive 9-23 → +1 天
            "timeZone": TZ,
        }

    def test_全天当天事件_start等于end_结束为一日后(self):
        event = _event(
            start=datetime(2026, 9, 20, 16, 0, tzinfo=UTC),
            end=datetime(2026, 9, 20, 16, 0, tzinfo=UTC),  # 当天事件
            all_day=True,
        )

        payload = local_event_to_graph_payload(event, tz=TZ)

        assert payload["start"]["dateTime"] == "2026-09-21T00:00:00"
        assert payload["end"]["dateTime"] == "2026-09-22T00:00:00"

    def test_描述与地点挂到body与location(self):
        event = _event(description="带医保卡", location="诊所")

        payload = local_event_to_graph_payload(event, tz=TZ)

        assert payload["body"] == {"contentType": "text", "content": "带医保卡"}
        assert payload["location"] == {"displayName": "诊所"}

    def test_空描述与地点省略键(self):
        payload = local_event_to_graph_payload(_event(), tz=TZ)

        assert "body" not in payload
        assert "location" not in payload


class TestContentHash:
    """内容摘要测试 (本地是否已改判定)."""

    def test_业务字段变更哈希变化(self):
        base = _event()
        changed = _event(title="复诊")

        assert calendar_content_hash(base) != calendar_content_hash(changed)

    def test_非业务字段变更哈希不变(self):
        base = _event()
        same = _event()
        same.updated_at = datetime(2026, 10, 1, tzinfo=UTC)

        assert calendar_content_hash(base) == calendar_content_hash(same)


class TestComputePlan:
    """单向 push + 镜像修复同步计划测试."""

    def test_双方一致_空计划(self):
        event = _event()
        mapping = _mapping(
            content_hash=calendar_content_hash(event),
            last_synced_at=datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
        )

        plan = compute_calendar_sync_plan(
            [event],
            [_remote()],
            [mapping],
            tz=TZ,
        )

        assert plan.actions == []
        assert plan.foreign_remote_ids == []

    def test_本地软取消_远端删除(self):
        event = _event(status=CalendarEventStatus.CANCELLED)
        mapping = _mapping(content_hash=calendar_content_hash(event))

        plan = compute_calendar_sync_plan([event], [_remote()], [mapping], tz=TZ)

        assert [a.kind for a in plan.actions] == ["push_delete"]
        assert plan.actions[0].remote_id == "rid-1"

    def test_本地硬删除_远端删除(self):
        mapping = _mapping(local_id=99, remote_id="rid-x")

        plan = compute_calendar_sync_plan([], [_remote(rid="rid-x")], [mapping], tz=TZ)

        assert [a.kind for a in plan.actions] == ["push_delete"]
        assert plan.actions[0].remote_id == "rid-x"

    def test_本地内容变更_推送更新(self):
        event = _event(title="复诊")
        mapping = _mapping(content_hash=calendar_content_hash(_event(title="牙医")))

        plan = compute_calendar_sync_plan([event], [_remote()], [mapping], tz=TZ)

        assert [a.kind for a in plan.actions] == ["push_update"]
        assert plan.actions[0].payload["subject"] == "复诊"

    def test_远端被外部编辑_用本地内容revert(self):
        event = _event()
        mapping = _mapping(content_hash=calendar_content_hash(event))
        # 远端 lastModified 晚于上次同步 = 外部编辑
        remote = _remote(last_modified="2026-09-16T08:00:00.0000000Z")

        plan = compute_calendar_sync_plan([event], [remote], [mapping], tz=TZ)

        assert [a.kind for a in plan.actions] == ["push_update"]
        assert plan.actions[0].payload["subject"] == "牙医"

    def test_远端被删除_重新POST保持镜像(self):
        event = _event()
        mapping = _mapping(content_hash=calendar_content_hash(event))

        plan = compute_calendar_sync_plan([event], [], [mapping], tz=TZ)

        assert [a.kind for a in plan.actions] == ["push_create"]
        assert plan.actions[0].local_id == 1

    def test_双方都变_本地权威推送本地内容(self):
        event = _event(title="本地改的")
        mapping = _mapping(content_hash=calendar_content_hash(_event()))
        remote = _remote(last_modified="2026-09-16T08:00:00.0000000Z")

        plan = compute_calendar_sync_plan([event], [remote], [mapping], tz=TZ)

        assert [a.kind for a in plan.actions] == ["push_update"]
        assert plan.actions[0].payload["subject"] == "本地改的"

    def test_未映射active事件_推送创建(self):
        event = _event(id=7)

        plan = compute_calendar_sync_plan([event], [], [], tz=TZ)

        assert [a.kind for a in plan.actions] == ["push_create"]
        assert plan.actions[0].local_id == 7

    def test_未映射软取消事件_无动作(self):
        event = _event(status=CalendarEventStatus.CANCELLED)

        plan = compute_calendar_sync_plan([event], [], [], tz=TZ)

        assert plan.actions == []

    def test_未映射重复事件_不同步(self):
        event = _event(recurrence_rule="FREQ=WEEKLY;INTERVAL=1")

        plan = compute_calendar_sync_plan([event], [], [], tz=TZ)

        assert plan.actions == []

    def test_已映射事件变重复_保留远端并告警(self):
        event = _event(recurrence_rule="FREQ=WEEKLY;INTERVAL=1")
        mapping = _mapping(content_hash=calendar_content_hash(_event()))

        plan = compute_calendar_sync_plan([event], [_remote()], [mapping], tz=TZ)

        assert plan.actions == []
        assert plan.recurring_skipped_local_ids == [1]

    def test_容器内非映射远端条目_跳过并记录(self):
        # 用户在 Outlook 手建的事件, 映射表不认识 → 不动, 记 foreign
        event = _event()
        mapping = _mapping(content_hash=calendar_content_hash(event))

        plan = compute_calendar_sync_plan(
            [event],
            [_remote(), _remote(rid="rid-foreign", subject="手建")],
            [mapping],
            tz=TZ,
        )

        assert plan.actions == []
        assert plan.foreign_remote_ids == ["rid-foreign"]
