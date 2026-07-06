"""文件下载 HMAC 签名 URL - 无状态, 重启不丢失.

设计约定:
- 完全无状态: 服务端不存储 token, 仅通过 HMAC 验证
- 路径格式: {base_url}/{user_id}/{thread_id}/{agent_id}/{file_id}/{expiry}/{sig}/{filename}
  - 直连: base_url = http://host:port/v1/files/dl
  - CF Tunnel: base_url = https://domain/pc/v1/files/dl 或 /mac/v1/files/dl
  - base_url 由 FILE_SERVER_BASE_URL 环境变量控制, 完全配置驱动
- HMAC 输入: "{user_id}:{thread_id}:{agent_id}:{file_id}:{expiry}"
- 默认 30 天过期, 通过 env FILE_URL_TTL_DAYS 配置 (0 = 永久)
- secret 通过 env FILE_SIGNING_SECRET 强制配置, 启动缺失则 fail fast

使用方式:
    provider = get_signed_url_provider()
    token = provider.compose_token(user_id, thread_id, agent_id, file_id)
    # 调用方拼接 URL: f"{base_url}/{user_id}/{thread_id}/{agent_id}/{token}/{filename}"

    ok = provider.verify(user_id, thread_id, agent_id, file_id, expiry, sig)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time

logger = logging.getLogger(__name__)

_DEFAULT_TTL_DAYS = 30
_SIG_HEX_LEN = 32


def _to_bytes(secret: str | bytes) -> bytes:
    """统一 secret 为 bytes, str 时按 utf-8 编码."""
    if isinstance(secret, str):
        return secret.encode("utf-8")
    return secret


def _build_message(
    user_id: str,
    thread_id: str,
    agent_id: str,
    file_id: str,
    expiry: int,
) -> bytes:
    """构造 HMAC 输入消息."""
    return f"{user_id}:{thread_id}:{agent_id}:{file_id}:{expiry}".encode()


def _compute_sig(
    secret: bytes,
    user_id: str,
    thread_id: str,
    agent_id: str,
    file_id: str,
    expiry: int,
) -> str:
    """计算 HMAC-SHA256, 返回前 32 hex 字符 (128 bit, 足够防伪造)."""
    msg = _build_message(user_id, thread_id, agent_id, file_id, expiry)
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:_SIG_HEX_LEN]


class SignedURLProvider:
    """HMAC 签名 URL 提供者.

    线程安全 (不可变).
    """

    def __init__(
        self,
        secret: str | bytes,
        default_ttl_days: int = _DEFAULT_TTL_DAYS,
    ) -> None:
        if not secret:
            raise ValueError("secret 不能为空")
        self._secret = _to_bytes(secret)
        self._default_ttl_days = default_ttl_days

    def sign(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
        file_id: str,
        expiry: int,
    ) -> str:
        """计算签名, 返回 hex 字符串."""
        return _compute_sig(self._secret, user_id, thread_id, agent_id, file_id, expiry)

    def verify(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
        file_id: str,
        expiry: int,
        sig: str,
    ) -> bool:
        """验证签名 + 过期时间.

        Returns:
            True 表示签名匹配且未过期

        """
        if expiry > 0 and time.time() > expiry:
            return False
        expected = self.sign(user_id, thread_id, agent_id, file_id, expiry)
        return hmac.compare_digest(expected, sig)

    def compose_token(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
        file_id: str,
        ttl_days: int | None = None,
    ) -> str:
        """生成 "{file_id}/{expiry}/{sig}" 格式的 token 字符串.

        Args:
            ttl_days: None 使用默认值, 0 表示永久

        Returns:
            形如 "c65631d4/1751248000/a1b2c3d4..." 的字符串

        """
        days = self._default_ttl_days if ttl_days is None else ttl_days
        expiry = 0 if days <= 0 else int(time.time()) + days * 86400
        sig = self.sign(user_id, thread_id, agent_id, file_id, expiry)
        return f"{file_id}/{expiry}/{sig}"


_provider: SignedURLProvider | None = None
_provider_lock = threading.Lock()


def _get_signing_secret() -> str:
    """获取签名密钥, 缺失 fail fast."""
    try:
        from src.config.credentials_registry import require_credential

        return require_credential("file_signing_secret").strip()
    except Exception as e:
        logger.warning("读取 FILE_SIGNING_SECRET 失败: %s", e)
    raise RuntimeError(
        "FILE_SIGNING_SECRET 未设置. 请在 .env 中配置 (建议 >=32 字符随机字符串), "
        '例如: python -c "import secrets; print(secrets.token_urlsafe(32))"',
    )


def _get_url_ttl_days() -> int:
    """获取下载链接有效期天数."""
    try:
        from src.config.api_config import get_config as get_api_config

        return get_api_config().file_url_ttl_days
    except Exception as e:
        logger.warning(
            "读取 file_url_ttl_days 失败, 使用默认 %d: %s",
            _DEFAULT_TTL_DAYS,
            e,
        )
        return _DEFAULT_TTL_DAYS


def get_signed_url_provider() -> SignedURLProvider:
    """获取全局单例 (lazy init, 首次调用时读 env)."""
    global _provider
    if _provider is not None:
        return _provider
    with _provider_lock:
        if _provider is None:
            secret = _get_signing_secret()
            ttl = _get_url_ttl_days()
            _provider = SignedURLProvider(secret=secret, default_ttl_days=ttl)
            logger.info("SignedURLProvider 初始化: ttl_days=%d", ttl)
    return _provider


def reset_signed_url_provider_for_test(
    secret: str | bytes | None = None,
    ttl_days: int = _DEFAULT_TTL_DAYS,
) -> None:
    """重置全局 provider (仅供测试使用)."""
    global _provider
    with _provider_lock:
        if secret is None:
            _provider = None
        else:
            _provider = SignedURLProvider(secret=secret, default_ttl_days=ttl_days)
