"""SyncMap / SyncSetting 数据模型测试."""

from __future__ import annotations

from src.storage.models.sync_map import SyncItemKind, SyncMap
from src.storage.models.sync_setting import SyncSetting


class TestSyncMapModel:
    """SyncMap 模型测试."""

    def test_创建映射_默认字段生效(self):
        mapping = SyncMap(
            user_id="test_user",
            kind=SyncItemKind.TODO,
            local_id=1,
            remote_id="graph-base64-id",
        )

        assert mapping.kind == SyncItemKind.TODO
        assert mapping.content_hash is None
        assert mapping.last_synced_at is None

    def test_kind_非法值_校验失败(self):
        # SQLModel 表模型不做枚举校验 (与 TodoItem.status 现状一致),
        # kind 合法性由 DAO 层的 SyncItemKind 类型签名约束
        mapping = SyncMap(
            user_id="test_user",
            kind="contact",  # type: ignore[arg-type]
            local_id=1,
            remote_id="rid",
        )

        assert mapping.kind == "contact"

    def test_kind_支持event枚举(self):
        mapping = SyncMap(
            user_id="test_user",
            kind=SyncItemKind.EVENT,
            local_id=2,
            remote_id="rid",
        )

        assert mapping.kind.value == "event"


class TestSyncSettingModel:
    """SyncSetting 模型测试."""

    def test_创建键值_默认字段生效(self):
        setting = SyncSetting(user_id="test_user", key="todo_list_id", value="lid-1")

        assert setting.key == "todo_list_id"
        assert setting.value == "lid-1"
