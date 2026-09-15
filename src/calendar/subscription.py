"""日历订阅 token 工具.

生成与哈希: 明文 token 仅创建时返回, 库中只存 sha256.
校验按 (token_hash, user_id) 查库, 撤销 = revoked_at 非 None.
"""

from __future__ import annotations

import hashlib
import secrets

_TOKEN_PREFIX = "cal_"


def generate_subscription_token() -> str:
    """生成随机订阅 token (URL 安全, 32 字符熵)."""
    return _TOKEN_PREFIX + secrets.token_urlsafe(24)


def hash_subscription_token(token: str) -> str:
    """计算 token 的 sha256 hex."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = ["generate_subscription_token", "hash_subscription_token"]
