"""SignedURLProvider 单元测试.

测试 HMAC 签名 URL 的签发、验证、过期和篡改场景.
"""

from __future__ import annotations

import time

import pytest

from src.files.signed_url import (
    SignedURLProvider,
    reset_signed_url_provider_for_test,
)

SECRET_A = "test-secret-key-a-32-chars-long"
SECRET_B = "another-secret-key-b-different"


@pytest.fixture(autouse=True)
def _reset_provider():
    """每条用例前后确保全局 provider 已重置."""
    reset_signed_url_provider_for_test()
    yield
    reset_signed_url_provider_for_test()


class TestSignAndVerify:
    """签名/验证基本流程."""

    def test_sign_returns_consistent_hex(self):
        provider = SignedURLProvider(secret=SECRET_A)
        sig = provider.sign("u1", "t1", "a1", "f1", 2000000000)
        assert isinstance(sig, str)
        assert len(sig) == 32
        assert all(c in "0123456789abcdef" for c in sig)

    def test_verify_with_correct_inputs_returns_true(self):
        provider = SignedURLProvider(secret=SECRET_A)
        sig = provider.sign("u1", "t1", "a1", "f1", 2000000000)
        assert provider.verify("u1", "t1", "a1", "f1", 2000000000, sig) is True

    def test_verify_with_different_secret_returns_false(self):
        provider_a = SignedURLProvider(secret=SECRET_A)
        provider_b = SignedURLProvider(secret=SECRET_B)
        sig = provider_a.sign("u1", "t1", "a1", "f1", 2000000000)
        assert provider_b.verify("u1", "t1", "a1", "f1", 2000000000, sig) is False

    def test_verify_with_tampered_user_id_returns_false(self):
        provider = SignedURLProvider(secret=SECRET_A)
        sig = provider.sign("u1", "t1", "a1", "f1", 2000000000)
        assert provider.verify("u2", "t1", "a1", "f1", 2000000000, sig) is False

    def test_verify_with_tampered_file_id_returns_false(self):
        provider = SignedURLProvider(secret=SECRET_A)
        sig = provider.sign("u1", "t1", "a1", "f1", 2000000000)
        assert provider.verify("u1", "t1", "a1", "f2", 2000000000, sig) is False

    def test_verify_with_tampered_expiry_returns_false(self):
        provider = SignedURLProvider(secret=SECRET_A)
        sig = provider.sign("u1", "t1", "a1", "f1", 2000000000)
        assert provider.verify("u1", "t1", "a1", "f1", 2000000001, sig) is False


class TestExpiry:
    """过期处理."""

    def test_verify_expired_returns_false(self):
        provider = SignedURLProvider(secret=SECRET_A)
        past = int(time.time()) - 100
        sig = provider.sign("u1", "t1", "a1", "f1", past)
        assert provider.verify("u1", "t1", "a1", "f1", past, sig) is False

    def test_expiry_zero_means_never_expire(self):
        provider = SignedURLProvider(secret=SECRET_A)
        sig = provider.sign("u1", "t1", "a1", "f1", 0)
        assert provider.verify("u1", "t1", "a1", "f1", 0, sig) is True


class TestComposeToken:
    """token 拼装."""

    def test_compose_token_format(self):
        provider = SignedURLProvider(secret=SECRET_A, default_ttl_days=7)
        token = provider.compose_token("u1", "t1", "a1", "abc12345")
        parts = token.split("/")
        assert len(parts) == 3
        assert parts[0] == "abc12345"
        assert int(parts[1]) > int(time.time())
        assert len(parts[2]) == 32

    def test_compose_token_permanent_when_ttl_zero(self):
        provider = SignedURLProvider(secret=SECRET_A, default_ttl_days=0)
        token = provider.compose_token("u1", "t1", "a1", "abc12345")
        parts = token.split("/")
        assert parts[1] == "0"

    def test_compose_token_override_ttl(self):
        provider = SignedURLProvider(secret=SECRET_A, default_ttl_days=30)
        token_default = provider.compose_token("u1", "t1", "a1", "abc12345")
        token_permanent = provider.compose_token("u1", "t1", "a1", "abc12345", ttl_days=0)
        assert token_default.split("/")[1] != "0"
        assert token_permanent.split("/")[1] == "0"

    def test_compose_and_verify_roundtrip(self):
        provider = SignedURLProvider(secret=SECRET_A)
        token = provider.compose_token("u1", "t1", "a1", "abc12345")
        file_id, expiry, sig = token.split("/")
        assert provider.verify("u1", "t1", "a1", file_id, int(expiry), sig)


class TestProviderInit:
    """构造函数与 secret 校验."""

    def test_empty_secret_raises(self):
        with pytest.raises(ValueError, match="secret"):
            SignedURLProvider(secret="")

    def test_secret_accepts_bytes(self):
        provider = SignedURLProvider(secret=b"binary-secret-key-32-chars")
        sig = provider.sign("u", "t", "a", "f", 0)
        assert provider.verify("u", "t", "a", "f", 0, sig)
