"""doc2md 文档解析客户端 - HTTP 调用 doc2md 服务 (bytes → markdown).

对齐 browser_renderer 模式: 模块级单例 + httpx.AsyncClient 懒加载 +
运行时关闭. 服务地址 DOC2MD_BASE_URL (runtime_env), 鉴权 token
DOC2MD_TOKEN (credentials_registry); 未配置地址时 is_configured=False,
调用方降级为提示行 (与现状行为一致).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, field

import httpx

from src.config.runtime_env import get_doc2md_base_url

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 5.0
# OCR 全量转换分钟级, 预算 30 分钟
_REQUEST_TIMEOUT = 1800.0


class Doc2mdError(RuntimeError):
    """doc2md 服务调用失败."""


@dataclass
class Doc2mdResult:
    """转换结果 (doc2md /convert 响应)."""

    markdown: str
    mode: str  # digital-docx / digital-pdf / ocr / scan-deferred
    pages: int = 0
    elapsed: float = 0.0
    images: list[dict] = field(default_factory=list)  # [{"name", "content_b64"}]

    @property
    def deferred(self) -> bool:
        """扫描件延迟模式 (内容未转换, 需后台再调 defer_scan=False)."""
        return self.mode == "scan-deferred"


class Doc2mdClient:
    """doc2md HTTP 客户端."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url or get_doc2md_base_url()
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    def is_configured(self) -> bool:
        """服务是否已配置 (未配置时二进制文档解析降级)."""
        return bool(self._base_url)

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=httpx.Timeout(_REQUEST_TIMEOUT, connect=_CONNECT_TIMEOUT),
                )
            return self._client

    async def close(self) -> None:
        """关闭 HTTP 客户端."""
        async with self._lock:
            if self._client is not None and not self._client.is_closed:
                await self._client.aclose()
            self._client = None

    async def convert(
        self,
        filename: str,
        data: bytes,
        *,
        defer_scan: bool = False,
    ) -> Doc2mdResult | None:
        """转换二进制文档为 Markdown.

        Args:
            filename: 原始文件名 (含扩展名, 决定服务端分流)
            data: 文件字节内容
            defer_scan: 扫描件延迟模式 — 检测到扫描件立即返回页数不跑 OCR

        Returns:
            转换结果; 服务未配置时返回 None (调用方降级)

        Raises:
            Doc2mdError: 服务调用失败 (网络/4xx/5xx/响应异常)

        """
        if not self.is_configured():
            return None

        from src.config.credentials_registry import get_credential

        token = get_credential("doc2md_token") or ""
        payload = {
            "filename": filename,
            "content_b64": base64.b64encode(data).decode("ascii"),
            "defer_scan": defer_scan,
        }

        try:
            client = await self._get_client()
            response = await client.post(
                "/convert",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise Doc2mdError(
                f"doc2md 服务错误: {e.response.status_code} {e.response.text[:200]}"
            ) from e
        except httpx.HTTPError as e:
            raise Doc2mdError(f"doc2md 服务不可达: {e}") from e

        body = response.json()
        if not body.get("success"):
            raise Doc2mdError(f"doc2md 转换失败: {body.get('detail', '未知错误')}")

        return Doc2mdResult(
            markdown=body.get("markdown", ""),
            mode=body.get("mode", ""),
            pages=body.get("pages", 0),
            elapsed=body.get("elapsed", 0.0),
            images=body.get("images", []),
        )


_client: Doc2mdClient | None = None


def get_doc2md_client() -> Doc2mdClient:
    """获取 Doc2mdClient 进程级单例."""
    global _client
    if _client is None:
        _client = Doc2mdClient()
    return _client


async def close_doc2md_client() -> None:
    """关闭 HTTP 客户端单例."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None


__all__ = [
    "Doc2mdClient",
    "Doc2mdError",
    "Doc2mdResult",
    "close_doc2md_client",
    "get_doc2md_client",
]
