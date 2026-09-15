"""日历订阅 token 单元测试.

验证 token 生成/哈希工具与 CalendarService 订阅管理方法:
创建 (明文只返回一次) / 校验 / 撤销 / 拉取时间戳.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.calendar.subscription import (
    generate_subscription_token,
    hash_subscription_token,
)
from src.storage.models.calendar_subscription import CalendarSubscription
from src.storage.service.calendar_service import CalendarService


@pytest.fixture
def mock_session_factory():
    return MagicMock()


@pytest.fixture
def service(mock_session_factory):
    return CalendarService(mock_session_factory, user_id="user1")


@pytest.fixture
def sample_subscription():
    return CalendarSubscription(
        user_id="user1",
        token_hash="a" * 64,
    )


class TestTokenUtils:
    """token 生成与哈希."""

    def test_generated_token_has_prefix_and_entropy(self) -> None:
        token = generate_subscription_token()
        assert token.startswith("cal_")
        assert len(token) > 20

    def test_tokens_are_unique(self) -> None:
        assert generate_subscription_token() != generate_subscription_token()

    def test_hash_is_sha256_hex(self) -> None:
        digest = hash_subscription_token("cal_abc")
        assert len(digest) == 64
        int(digest, 16)  # 合法 hex


class TestCreateSubscription:
    """测试创建订阅."""

    @pytest.mark.asyncio
    async def test_create_returns_record_and_plaintext(
        self,
        service,
        sample_subscription,
    ):
        with patch.object(
            service.subscription_dao,
            "create_subscription",
            return_value=sample_subscription,
        ) as mock_create:
            record, plaintext = await service.create_subscription()

        assert record == sample_subscription
        assert plaintext.startswith("cal_")
        stored_hash = mock_create.call_args.kwargs["token_hash"]
        assert stored_hash == hash_subscription_token(plaintext)
        assert mock_create.call_args.kwargs["user_id"] == "user1"

    @pytest.mark.asyncio
    async def test_plaintext_not_equal_hash(self, service, sample_subscription):
        with patch.object(
            service.subscription_dao,
            "create_subscription",
            return_value=sample_subscription,
        ):
            _, plaintext = await service.create_subscription()
        assert plaintext != sample_subscription.token_hash


class TestVerifySubscriptionToken:
    """测试 token 校验."""

    @pytest.mark.asyncio
    async def test_valid_token_returns_record(
        self,
        service,
        sample_subscription,
    ):
        token = "cal_valid_token"
        sample_subscription.token_hash = hash_subscription_token(token)
        with patch.object(
            service.subscription_dao,
            "find_active_by_hash",
            return_value=sample_subscription,
        ) as mock_find:
            result = await service.verify_subscription_token(token)

        assert result == sample_subscription
        mock_find.assert_called_once_with(hash_subscription_token(token))

    @pytest.mark.asyncio
    async def test_unknown_token_returns_none(self, service):
        with patch.object(
            service.subscription_dao,
            "find_active_by_hash",
            return_value=None,
        ):
            assert await service.verify_subscription_token("cal_unknown") is None

    @pytest.mark.asyncio
    async def test_revoked_token_returns_none(self, service, sample_subscription):
        """撤销的订阅不可再用于拉取."""
        sample_subscription.revoked_at = datetime(2026, 1, 1, tzinfo=UTC)
        with patch.object(
            service.subscription_dao,
            "find_active_by_hash",
            return_value=None,
        ):
            assert await service.verify_subscription_token("cal_revoked") is None


class TestRevokeSubscription:
    """测试撤销订阅."""

    @pytest.mark.asyncio
    async def test_revoke_owned(self, service, sample_subscription):
        with (
            patch.object(
                service.subscription_dao,
                "get_by_subscription_id",
                return_value=sample_subscription,
            ),
            patch.object(
                service.subscription_dao,
                "revoke_subscription",
                return_value=True,
            ) as mock_revoke,
        ):
            assert await service.revoke_subscription("sub_abc12345") is True
        mock_revoke.assert_called_once_with("sub_abc12345")

    @pytest.mark.asyncio
    async def test_revoke_other_user_returns_false(
        self,
        service,
        sample_subscription,
    ):
        sample_subscription.user_id = "user2"
        with (
            patch.object(
                service.subscription_dao,
                "get_by_subscription_id",
                return_value=sample_subscription,
            ),
            patch.object(
                service.subscription_dao,
                "revoke_subscription",
                new=AsyncMock(),
            ) as mock_revoke,
        ):
            assert await service.revoke_subscription("sub_abc12345") is False
        mock_revoke.assert_not_called()


class TestTouchSubscription:
    """测试拉取时间戳更新 (节流写入)."""

    @pytest.mark.asyncio
    async def test_touch_updates_last_access(self, service, sample_subscription):
        with patch.object(
            service.subscription_dao,
            "touch",
            new=AsyncMock(),
        ) as mock_touch:
            await service.touch_subscription(sample_subscription)
        mock_touch.assert_called_once()
        update_data = mock_touch.call_args.args[1]
        assert "last_access_at" in update_data
