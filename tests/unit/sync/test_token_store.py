"""GraphTokenStore 单元测试.

覆盖:
- save/load 往返一致, 原子覆盖无 .tmp 残留
- 文件 0600 / 目录 0700 权限
- load 文件不存在返回 None
- delete 存在与不存在两种情形
"""

from __future__ import annotations

import stat
from pathlib import Path

from src.sync.token_store import GraphTokenStore


def _store(tmp_path: Path, user_id: str = "test_user") -> GraphTokenStore:
    return GraphTokenStore(user_id, base_path=tmp_path)


class TestGraphTokenStore:
    """GraphTokenStore 测试."""

    def test_save_写入token后load_往返一致(self, tmp_path):
        store = _store(tmp_path)
        tokens = {"access_token": "at-1", "refresh_token": "rt-1"}

        store.save(tokens)

        assert store.load() == tokens

    def test_save_二次写入_原子覆盖且无tmp残留(self, tmp_path):
        store = _store(tmp_path)
        store.save({"access_token": "at-1", "refresh_token": "rt-1"})

        store.save({"access_token": "at-2", "refresh_token": "rt-2"})

        assert store.load() == {
            "access_token": "at-2",
            "refresh_token": "rt-2",
        }
        leftovers = [p.name for p in tmp_path.rglob("*tmp*")]
        assert leftovers == []

    def test_save_新建文件与目录_权限为0600与0700(self, tmp_path):
        store = _store(tmp_path)

        store.save({"access_token": "at", "refresh_token": "rt"})

        file_mode = stat.S_IMODE(store.path.stat().st_mode)
        dir_mode = stat.S_IMODE(store.path.parent.stat().st_mode)
        assert file_mode == 0o600
        assert dir_mode == 0o700

    def test_load_token文件不存在_返回None(self, tmp_path):
        store = _store(tmp_path)

        assert store.load() is None
        assert store.exists() is False

    def test_delete_token文件存在_删除并返回True(self, tmp_path):
        store = _store(tmp_path)
        store.save({"access_token": "at", "refresh_token": "rt"})

        assert store.delete() is True
        assert store.exists() is False
        assert store.load() is None

    def test_delete_token文件不存在_返回False(self, tmp_path):
        store = _store(tmp_path)

        assert store.delete() is False

    def test_init_非法userid_拒绝路径穿越(self, tmp_path):
        import pytest

        with pytest.raises(ValueError, match="用户ID"):
            GraphTokenStore("../escape", base_path=tmp_path)
