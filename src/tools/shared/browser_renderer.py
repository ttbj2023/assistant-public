"""Chromium 浏览器渲染器 - HTTP 客户端, 调用 tool-runtime 容器渲染.

渲染服务运行在 tool-runtime 附属容器 (TOOL_RUNTIME_BASE_URL):
- /render/pdf: HTML → PDF
- /render/png: HTML → PNG (通用截图)
- /render/chart: 图表源码 → PNG (内部构建 HTML, mermaid/vega_lite/markmap)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from src.config.runtime_env import get_tool_runtime_base_url

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 5.0


class BrowserRenderer:
    """HTTP 渲染客户端, 接口兼容原本地 Playwright 版本."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url or get_tool_runtime_base_url()
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=httpx.Timeout(120.0, connect=_CONNECT_TIMEOUT),
                )
            return self._client

    async def close(self) -> None:
        async with self._lock:
            if self._client is not None and not self._client.is_closed:
                await self._client.aclose()
                self._client = None

    async def render_to_pdf(
        self,
        html_content: str,
        output_path: Path,
        *,
        block_external: bool = True,
        timeout_seconds: float = 60.0,
    ) -> None:
        """HTML → PDF (通过 tool-runtime /render/pdf)."""
        client = await self._get_client()
        payload = {
            "html_content": html_content,
            "block_external": block_external,
            "timeout_seconds": timeout_seconds,
        }
        response = await client.post("/render/pdf", json=payload)
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            raise RuntimeError(f"PDF渲染失败: {data.get('error', '未知错误')}")

        import base64

        content = base64.b64decode(data["content_b64"])
        await asyncio.to_thread(output_path.write_bytes, content)

    async def render_to_png(
        self,
        html_content: str,
        output_path: Path,
        *,
        selector: str = ".chart-container",
        viewport: dict[str, int] | None = None,
        scale: int = 3,
        timeout: float = 30.0,
    ) -> Path:
        """HTML → PNG (通过 tool-runtime /render/png)."""
        client = await self._get_client()
        vp = viewport or {"width": 1400, "height": 1000}
        payload = {
            "html_content": html_content,
            "selector": selector,
            "viewport_width": vp["width"],
            "viewport_height": vp["height"],
            "scale": scale,
            "timeout_seconds": timeout,
        }
        response = await client.post("/render/png", json=payload)
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            raise RuntimeError(f"PNG渲染失败: {data.get('error', '未知错误')}")

        import base64

        content = base64.b64decode(data["content_b64"])
        await asyncio.to_thread(output_path.write_bytes, content)
        return output_path

    async def render_chart(
        self,
        *,
        engine: str,
        code: str,
        title: str | None,
        width: int | None,
        height: int | None,
        scale: int,
        output_path: Path,
    ) -> Path:
        """图表源码 → PNG (通过 tool-runtime /render/chart).

        HTML 构建在 tool-runtime 内完成, app 端无需加载 JS 库.
        """
        client = await self._get_client()
        payload: dict[str, Any] = {
            "engine": engine,
            "code": code,
            "scale": scale,
        }
        if title is not None:
            payload["title"] = title
        if width is not None:
            payload["width"] = width
        if height is not None:
            payload["height"] = height

        response = await client.post("/render/chart", json=payload)
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            raise RuntimeError(f"图表渲染失败: {data.get('error', '未知错误')}")

        import base64

        content = base64.b64decode(data["content_b64"])
        await asyncio.to_thread(output_path.write_bytes, content)
        return output_path


_renderer: BrowserRenderer | None = None


def get_browser_renderer() -> BrowserRenderer:
    """获取 BrowserRenderer 进程级单例."""
    global _renderer
    if _renderer is None:
        _renderer = BrowserRenderer()
    return _renderer


async def close_browser_renderer() -> None:
    """关闭 HTTP 客户端单例."""
    global _renderer
    if _renderer is not None:
        await _renderer.close()
        _renderer = None


__all__ = [
    "BrowserRenderer",
    "close_browser_renderer",
    "get_browser_renderer",
]
