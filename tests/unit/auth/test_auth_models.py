"""认证数据模型单元测试.

覆盖 src/auth/models.py 和 src/auth/user_models.py 的核心逻辑:
- UserStatus 枚举
- ApiKeyInfo 验证
- StaticUser 验证和方法
- StaticUserConfig 查询和验证
- AuthUser / AuthContext / AuthRequest / AuthResponse
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.auth.models import (
    ApiKeyInfo,
    AuthContext,
    AuthUser,
    StaticUser,
    StaticUserConfig,
    UserStatus,
)


class TestApiKeyInfoModels:
    """API密钥模型验证测试 (models.py + user_models.py)."""

    def test_valid_key(self) -> None:
        info = ApiKeyInfo(
            api_key="sk-project-alice-main-abc123",
            thread_id="main",
        )
        assert info.api_key.startswith("sk-project-")
        assert info.thread_id == "main"

    def test_invalid_prefix_raises(self) -> None:
        with pytest.raises(ValueError, match="sk-project-"):
            ApiKeyInfo(api_key="invalid-key", thread_id="main")

    def test_too_few_parts_raises(self) -> None:
        with pytest.raises(Exception, match=r"sk-project-|至少4个部分"):
            ApiKeyInfo(api_key="sk-project", thread_id="main")

    def test_empty_thread_id_raises(self) -> None:
        with pytest.raises(Exception):
            ApiKeyInfo(api_key="sk-project-a-b-c", thread_id="")


class TestStaticUserModel:
    """StaticUser 模型测试."""

    def test_empty_user_id_raises(self) -> None:
        with pytest.raises(Exception):
            StaticUser(user_id="", display_name="Alice")

    def test_empty_display_name_raises(self) -> None:
        with pytest.raises(Exception):
            StaticUser(user_id="alice", display_name="")

    def test_inactive_user(self) -> None:
        user = StaticUser(
            user_id="bob",
            display_name="Bob",
            status=UserStatus.INACTIVE,
        )
        assert not user.is_active()

    def test_get_api_key_info(self) -> None:
        key = ApiKeyInfo(api_key="sk-project-a-b-c", thread_id="main")
        user = StaticUser(
            user_id="alice",
            display_name="Alice",
            threads=[key],
        )
        found = user.get_api_key_info("sk-project-a-b-c")
        assert found is not None
        assert found.thread_id == "main"

    def test_get_thread_ids(self) -> None:
        k1 = ApiKeyInfo(api_key="sk-project-a-b-1", thread_id="main")
        k2 = ApiKeyInfo(api_key="sk-project-a-b-2", thread_id="work")
        user = StaticUser(user_id="alice", display_name="Alice", threads=[k1, k2])
        assert user.get_thread_ids() == ["main", "work"]

    def test_update_usage(self) -> None:
        key = ApiKeyInfo(api_key="sk-project-a-b-c", thread_id="main")
        user = StaticUser(user_id="alice", display_name="Alice", threads=[key])
        assert user.update_usage("sk-project-a-b-c") is True
        assert user.threads[0].usage_count == 1
        assert user.update_usage("unknown") is False

    def test_is_api_key_expired_active(self) -> None:
        key = ApiKeyInfo(api_key="sk-project-a-b-c", thread_id="main")
        user = StaticUser(user_id="alice", display_name="Alice", threads=[key])
        assert not user.is_api_key_expired("sk-project-a-b-c")

    def test_is_api_key_expired_inactive(self) -> None:
        key = ApiKeyInfo(api_key="sk-project-a-b-c", thread_id="main", is_active=False)
        user = StaticUser(user_id="alice", display_name="Alice", threads=[key])
        assert user.is_api_key_expired("sk-project-a-b-c")

    def test_is_api_key_expired_by_time(self) -> None:
        key = ApiKeyInfo(
            api_key="sk-project-a-b-c",
            thread_id="main",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        user = StaticUser(user_id="alice", display_name="Alice", threads=[key])
        assert user.is_api_key_expired("sk-project-a-b-c")

    def test_is_api_key_expired_not_found(self) -> None:
        user = StaticUser(user_id="alice", display_name="Alice")
        assert user.is_api_key_expired("unknown")

    def test_add_api_key_new(self) -> None:
        user = StaticUser(user_id="alice", display_name="Alice")
        key = ApiKeyInfo(api_key="sk-project-a-b-c", thread_id="main")
        assert user.add_api_key(key) is True
        assert len(user.threads) == 1

    def test_add_api_key_update_existing_thread(self) -> None:
        k1 = ApiKeyInfo(api_key="sk-project-a-b-old", thread_id="main")
        user = StaticUser(user_id="alice", display_name="Alice", threads=[k1])
        k2 = ApiKeyInfo(api_key="sk-project-a-b-new", thread_id="main")
        assert user.add_api_key(k2) is True
        assert user.threads[0].api_key == "sk-project-a-b-new"

    def test_add_api_key_duplicate_key(self) -> None:
        k1 = ApiKeyInfo(api_key="sk-project-a-b-same", thread_id="main")
        user = StaticUser(user_id="alice", display_name="Alice", threads=[k1])
        k2 = ApiKeyInfo(api_key="sk-project-a-b-same", thread_id="work")
        assert user.add_api_key(k2) is False

    def test_deactivate_api_key(self) -> None:
        key = ApiKeyInfo(api_key="sk-project-a-b-c", thread_id="main")
        user = StaticUser(user_id="alice", display_name="Alice", threads=[key])
        assert user.deactivate_api_key("sk-project-a-b-c") is True
        assert not user.threads[0].is_active

    def test_deactivate_api_key_not_found(self) -> None:
        user = StaticUser(user_id="alice", display_name="Alice")
        assert user.deactivate_api_key("unknown") is False


class TestStaticUserConfig:
    """StaticUserConfig 测试."""

    def _make_config(self) -> StaticUserConfig:
        key = ApiKeyInfo(api_key="sk-project-alice-main-abc", thread_id="main")
        user = StaticUser(
            user_id="alice",
            display_name="Alice",
            threads=[key],
        )
        return StaticUserConfig(users={"alice": user})

    def test_get_user_by_api_key(self) -> None:
        config = self._make_config()
        result = config.get_user_by_api_key("sk-project-alice-main-abc")
        assert result is not None
        user, info = result
        assert user.user_id == "alice"

    def test_get_user_by_api_key_not_found(self) -> None:
        config = self._make_config()
        assert config.get_user_by_api_key("unknown") is None

    def test_get_user_by_user_id(self) -> None:
        config = self._make_config()
        assert config.get_user_by_user_id("alice") is not None
        assert config.get_user_by_user_id("bob") is None

    def test_get_all_api_keys(self) -> None:
        config = self._make_config()
        keys = config.get_all_api_keys()
        assert "sk-project-alice-main-abc" in keys

    def test_validate_api_key_active_user(self) -> None:
        config = self._make_config()
        result = config.validate_api_key("sk-project-alice-main-abc")
        assert result is not None

    def test_validate_api_key_inactive_user(self) -> None:
        key = ApiKeyInfo(api_key="sk-project-bob-main-xyz", thread_id="main")
        user = StaticUser(
            user_id="bob",
            display_name="Bob",
            status=UserStatus.INACTIVE,
            threads=[key],
        )
        config = StaticUserConfig(users={"bob": user})
        assert config.validate_api_key("sk-project-bob-main-xyz") is None

    def test_validate_api_key_invalid_key(self) -> None:
        config = self._make_config()
        assert config.validate_api_key("invalid") is None


class TestAuthModels:
    """AuthUser/AuthContext/AuthRequest/AuthResponse 测试."""

    def test_auth_user_permission(self) -> None:
        user = AuthUser(
            user_id="alice",
            thread_id="main",
            display_name="Alice",
            api_key="sk-project-a-b-c",
            permissions=["read", "write"],
        )
        assert user.has_permission("read") is True
        assert user.has_permission("admin") is False

    def test_auth_context(self) -> None:
        user = AuthUser(
            user_id="alice",
            thread_id="main",
            display_name="Alice",
            api_key="sk-project-a-b-c",
        )
        ctx = AuthContext(user=user, request_id="req-123")
        assert ctx.get_user_id() == "alice"
        assert ctx.get_thread_id() == "main"
