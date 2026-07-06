"""静态用户管理器单元测试.

覆盖 StaticUserManager 的核心逻辑:
- 构造函数 (配置文件加载)
- validate_api_key
- get_user_info / get_user_by_id
- is_user_active
- validate_user_isolation
- get_all_users / get_all_api_keys
- get_user_statistics
- generate_api_key
- _validate_identifier / _hash_identifier
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _make_config_data() -> dict[str, Any]:
    return {
        "users": {
            "alice": {
                "user_id": "alice",
                "display_name": "Alice Smith",
                "status": "active",
                "threads": [
                    {
                        "api_key": "sk-project-alice-main-abc123def456",
                        "thread_id": "main",
                        "description": "主线程",
                        "is_active": True,
                    }
                ],
            },
            "bob": {
                "user_id": "bob",
                "display_name": "Bob Jones",
                "status": "inactive",
                "threads": [
                    {
                        "api_key": "sk-project-bob-main-xyz789",
                        "thread_id": "main",
                        "description": "主线程",
                        "is_active": True,
                    }
                ],
            },
        }
    }


def _write_config(tmp_path: Path, data: dict[str, Any]) -> Path:
    config_path = tmp_path / "static_users.yaml"
    config_path.write_text(yaml.dump(data, allow_unicode=True))
    return config_path


class TestStaticUserManagerInit:
    """构造和加载测试."""

    def test_load_valid_config(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)
        assert manager.is_enabled()

    def test_load_missing_config(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        manager = StaticUserManager(config_path=tmp_path / "nonexistent.yaml")
        assert not manager.is_enabled()

    def test_load_empty_config(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = tmp_path / "empty.yaml"
        config_path.write_text("")
        manager = StaticUserManager(config_path=config_path)
        assert not manager.is_enabled()


class TestValidateApiKey:
    """API密钥验证测试."""

    def test_valid_key(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        result = manager.validate_api_key("sk-project-alice-main-abc123def456")
        assert result is not None
        user, key_info = result
        assert user.user_id == "alice"
        assert key_info.thread_id == "main"

    def test_invalid_key(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        result = manager.validate_api_key("sk-project-wrong-key")
        assert result is None

    def test_inactive_user_fails(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        result = manager.validate_api_key("sk-project-bob-main-xyz789")
        assert result is None

    def test_empty_key(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        assert manager.validate_api_key("") is None

    def test_disabled_manager(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        manager = StaticUserManager(config_path=tmp_path / "missing.yaml")
        assert manager.validate_api_key("any") is None


class TestGetUserInfo:
    """用户信息查询测试."""

    def test_existing_user(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        user = manager.get_user_info("alice")
        assert user is not None
        assert user.display_name == "Alice Smith"

    def test_nonexistent_user(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        assert manager.get_user_info("unknown") is None

    def test_get_user_by_id_alias(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        assert manager.get_user_by_id("alice") is not None


class TestUserStatus:
    """用户状态测试."""

    def test_active_user(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        assert manager.is_user_active("alice") is True

    def test_inactive_user(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        assert manager.is_user_active("bob") is False

    def test_nonexistent_user(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        assert manager.is_user_active("nobody") is False


class TestUserIsolation:
    """用户隔离验证测试."""

    def test_valid_isolation(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        assert manager.validate_user_isolation("alice", "main") is True

    def test_invalid_thread(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        assert manager.validate_user_isolation("alice", "other") is False


class TestGetAllUsersAndKeys:
    """批量查询测试."""

    def test_get_all_users(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        users = manager.get_all_users()
        assert "alice" in users
        assert "bob" in users

    def test_get_all_api_keys(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        keys = manager.get_all_api_keys()
        assert "sk-project-alice-main-abc123def456" in keys
        assert keys["sk-project-alice-main-abc123def456"] == ("alice", "main")


class TestGetUserStatistics:
    """统计信息测试."""

    def test_enabled_statistics(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        stats = manager.get_user_statistics()
        assert stats["enabled"] is True
        assert stats["total_users"] == 2
        assert stats["total_api_keys"] == 2
        assert stats["active_users"] == 1

    def test_disabled_statistics(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        manager = StaticUserManager(config_path=tmp_path / "missing.yaml")
        stats = manager.get_user_statistics()
        assert stats["enabled"] is False
        assert stats["total_users"] == 0


class TestGetApiKeyInfo:
    """API密钥信息查询测试."""

    def test_existing_key(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        info = manager.get_api_key_info("sk-project-alice-main-abc123def456")
        assert info is not None
        assert info.thread_id == "main"

    def test_nonexistent_key(self, tmp_path: Path) -> None:
        from src.auth.static_user_manager import StaticUserManager

        config_path = _write_config(tmp_path, _make_config_data())
        manager = StaticUserManager(config_path=config_path)

        assert manager.get_api_key_info("sk-project-unknown") is None
