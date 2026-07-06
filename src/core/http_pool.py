"""HTTP 连接池 - 共享 httpx.AsyncClient, 按 provider 复用.

Layer 2 纯基础设施, 不依赖任何业务模块 (inference / agent 等).
"""

from __future__ import annotations

import logging
from functools import lru_cache

import httpx

from src.core.lifecycle import register_resource

logger = logging.getLogger(__name__)

_HTTP_READ_TIMEOUT_LIMIT = 300.0


class HttpPool:
    """Provider 级 httpx.AsyncClient 连接池.

    设计原则:
    - HTTP 层 timeout 对走 http_async_client 的 provider 是实际生效值.
      openai SDK 检测到 http_client.timeout != 默认值(5s)时,
      会采用 http_client 的 timeout 而非 SDK 的 timeout 参数.
    - 每个 provider 一个 HTTP client, 不按 timeout 区分
    """

    def __init__(self) -> None:
        self._clients: dict[str, httpx.AsyncClient] = {}

    def get(
        self,
        provider: str,
        config: dict | None = None,
    ) -> httpx.AsyncClient:
        """获取 provider 对应的共享 AsyncClient.

        Args:
            provider: Provider 名称
            config: 可选的连接池配置 (max_keepalive_connections / max_connections / keepalive_expiry)

        Returns:
            复用的 httpx.AsyncClient 实例

        """
        if provider not in self._clients:
            if config:
                limits = httpx.Limits(
                    max_keepalive_connections=config.get(
                        "max_keepalive_connections",
                        20,
                    ),
                    max_connections=config.get("max_connections", 100),
                    keepalive_expiry=config.get("keepalive_expiry", 30.0),
                )
            else:
                limits = httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=100,
                    keepalive_expiry=30.0,
                )
            timeout_config = httpx.Timeout(
                connect=10.0,
                read=_HTTP_READ_TIMEOUT_LIMIT,
                write=10.0,
                pool=5.0,
            )
            self._clients[provider] = httpx.AsyncClient(
                timeout=timeout_config,
                limits=limits,
            )
            logger.info("🔧 创建共享 HTTP 客户端: %s", provider)

        return self._clients[provider]

    async def close_all(self) -> None:
        """关闭所有 HTTP 客户端."""
        for provider, client in self._clients.items():
            await client.aclose()
            logger.debug("🔌 关闭共享 HTTP 客户端: %s", provider)
        self._clients.clear()


@lru_cache(maxsize=1)
def get_http_pool() -> HttpPool:
    """获取全局 HttpPool 单例."""
    pool = HttpPool()
    register_resource("http_pool", pool.close_all)
    return pool
