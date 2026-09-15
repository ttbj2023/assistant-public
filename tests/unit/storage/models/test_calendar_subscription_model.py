"""CalendarSubscription 数据模型单元测试.

验证 token_hash 长度校验与大小写规范化.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.storage.models.calendar_subscription import CalendarSubscription


class TestTokenHashValidation:
    """token_hash 字段验证器测试."""

    def test_valid_hash_accepted(self) -> None:
        sub = CalendarSubscription(user_id="u", token_hash="a" * 64)
        assert sub.token_hash == "a" * 64

    def test_uppercase_hash_normalized_to_lower(self) -> None:
        sub = CalendarSubscription(user_id="u", token_hash="A" * 64)
        assert sub.token_hash == "a" * 64

    def test_wrong_length_raises(self) -> None:
        with pytest.raises(ValidationError, match="64 位 sha256 hex"):
            CalendarSubscription(user_id="u", token_hash="a" * 63)

    def test_default_subscription_id_generated(self) -> None:
        sub = CalendarSubscription(user_id="u", token_hash="a" * 64)
        assert sub.subscription_id.startswith("sub_")
        assert sub.revoked_at is None
