"""Tool Runtime - 通用工具运行时容器.

提供:
- /execute: Python代码执行 (skill执行器, openpyxl/numpy/pandas)
- /render/pdf: HTML → PDF (Playwright Chromium)
- /render/png: HTML → PNG (Playwright Chromium, 通用图表/截图)
- /render/chart: 图表源码 → PNG (内部构建 HTML, mermaid/vega_lite/markmap)
- /convert/pandoc: Markdown → HTML/DOCX (pandoc)
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Literal

from chart_builder import (
    build_markmap_html,
    build_mermaid_html,
    build_vega_lite_html,
)
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Assistant Tool Runtime", version="2.0.0")

WORKSPACE = Path("/workspace")
OUTPUT_DIR = WORKSPACE / "output"
MAX_FILE_BYTES = 50 * 1024 * 1024


# ───────────────────── 请求/响应模型 ─────────────────────


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"additionalProperties": False}
    )

    code: str = Field(min_length=1, max_length=50000)
    stdin: str = Field(default="", max_length=20000)
    timeout_seconds: float = Field(default=30.0, ge=0.5, le=120.0)
    max_stdout_chars: int = Field(default=20000, ge=1000, le=100000)
    max_stderr_chars: int = Field(default=20000, ge=1000, le=100000)
    collect_outputs: bool = Field(default=True)


class CreatedFile(BaseModel):
    filename: str
    relative_path: str
    size_bytes: int
    content_b64: str


class ExecuteResponse(BaseModel):
    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    created_files: list[CreatedFile] = []


class RenderPdfRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"additionalProperties": False}
    )

    html_content: str = Field(min_length=1)
    block_external: bool = Field(default=True)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=120.0)


class RenderPngRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"additionalProperties": False}
    )

    html_content: str = Field(min_length=1)
    selector: str = Field(default=".chart-container")
    viewport_width: int = Field(default=1400, ge=100, le=4000)
    viewport_height: int = Field(default=1000, ge=100, le=4000)
    scale: int = Field(default=3, ge=1, le=5)
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)


class RenderChartRequest(BaseModel):
    """图表渲染请求 - 内部完成 HTML 构建和渲染."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"additionalProperties": False}
    )

    engine: Literal["mermaid", "vega_lite", "markmap"]
    code: str = Field(min_length=1)
    title: str | None = None
    width: int | None = Field(default=None, ge=100, le=4000)
    height: int | None = Field(default=None, ge=100, le=4000)
    scale: int = Field(default=3, ge=1, le=5)


class PandocConvertRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"additionalProperties": False}
    )

    input_content: str = Field(min_length=1)
    reader: str = Field(default="gfm")
    writer: str = Field(default="html5")
    reference_doc_b64: str | None = Field(default=None)
    extra_args: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=120.0)


class RenderResponse(BaseModel):
    success: bool
    content_b64: str = ""
    size_bytes: int = 0
    error: str = ""


# ───────────────────── 端点 ─────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/execute")
async def execute(request: ExecuteRequest) -> ExecuteResponse:
    started = time.monotonic()
    script_path: Path | None = None
    timed_out = False

    _clean_output_dir()

    try:
        script_path = WORKSPACE / f".exec_{os.getpid()}_{int(started * 1000)}.py"
        script_path.write_text(request.code, encoding="utf-8")

        process = subprocess.Popen(
            ["python", "-I", str(script_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(WORKSPACE),
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )

        try:
            stdout, stderr = process.communicate(
                input=request.stdin,
                timeout=request.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()

        exit_code = process.returncode
        stdout, stdout_truncated = _truncate(stdout, request.max_stdout_chars)
        stderr, stderr_truncated = _truncate(stderr, request.max_stderr_chars)

        created_files = _collect_outputs() if request.collect_outputs else []

        return ExecuteResponse(
            success=(not timed_out and exit_code == 0),
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            created_files=created_files,
        )
    finally:
        if script_path is not None:
            script_path.unlink(missing_ok=True)


@app.post("/render/pdf")
async def render_pdf(request: RenderPdfRequest) -> RenderResponse:
    output_path = WORKSPACE / f".render_{os.getpid()}_{int(time.monotonic() * 1000)}.pdf"
    try:
        browser = await _browser_manager.get_browser()
        context = await browser.new_context()
        try:
            page = await context.new_page()
            if request.block_external:
                await page.route("**/*", _block_external_request)
            await page.set_content(
                request.html_content,
                wait_until="networkidle",
                timeout=request.timeout_seconds * 1000,
            )
            await page.emulate_media(media="print")
            await page.pdf(
                path=str(output_path),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                margin={
                    "top": "18mm",
                    "right": "18mm",
                    "bottom": "18mm",
                    "left": "18mm",
                },
            )
        finally:
            await context.close()

        if not output_path.exists():
            return RenderResponse(success=False, error="PDF文件未生成")

        content = output_path.read_bytes()
        return RenderResponse(
            success=True,
            content_b64=base64.b64encode(content).decode("ascii"),
            size_bytes=len(content),
        )
    except Exception as e:
        logger.exception("PDF渲染失败: %s", e)
        return RenderResponse(success=False, error=str(e))
    finally:
        output_path.unlink(missing_ok=True)


@app.post("/render/png")
async def render_png(request: RenderPngRequest) -> RenderResponse:
    try:
        content = await _playwright_render_png(
            html_content=request.html_content,
            selector=request.selector,
            viewport_width=request.viewport_width,
            viewport_height=request.viewport_height,
            scale=request.scale,
            timeout_seconds=request.timeout_seconds,
        )
        return RenderResponse(
            success=True,
            content_b64=base64.b64encode(content).decode("ascii"),
            size_bytes=len(content),
        )
    except Exception as e:
        logger.exception("PNG渲染失败: %s", e)
        return RenderResponse(success=False, error=str(e))


# 引擎 → 内层截图选择器 (紧贴内容, 避免外层 min-width:100% 撑出空白)
_CHART_SELECTORS = {
    "mermaid": ".mermaid-content",
    "vega_lite": ".vega-content",
    "markmap": ".markmap-content",
}


@app.post("/render/chart")
async def render_chart(request: RenderChartRequest) -> RenderResponse:
    """图表渲染端点: 源码 → PNG (内部构建 HTML + 渲染).

    与 /render/png 区别: app 端无需构建 HTML, 仅传图表源码和引擎类型.
    """
    try:
        if request.engine == "mermaid":
            html = build_mermaid_html(code=request.code, title=request.title)
        elif request.engine == "vega_lite":
            html = build_vega_lite_html(
                spec_json=request.code,
                title=request.title,
                width=request.width,
                height=request.height,
            )
        elif request.engine == "markmap":
            html = build_markmap_html(
                markdown=request.code,
                title=request.title,
                width=request.width,
                height=request.height,
            )
        else:
            # Literal 类型保护, 理论上不可达
            return RenderResponse(
                success=False, error=f"不支持的引擎: {request.engine}"
            )
    except Exception as e:
        logger.exception("chart HTML 构建失败: %s", e)
        return RenderResponse(success=False, error=str(e))

    try:
        content = await _playwright_render_png(
            html_content=html,
            selector=_CHART_SELECTORS[request.engine],
            viewport_width=1400,
            viewport_height=1000,
            scale=request.scale,
            timeout_seconds=30.0,
        )
        return RenderResponse(
            success=True,
            content_b64=base64.b64encode(content).decode("ascii"),
            size_bytes=len(content),
        )
    except Exception as e:
        logger.exception("chart 渲染失败: %s", e)
        return RenderResponse(success=False, error=str(e))


@app.post("/convert/pandoc")
async def pandoc_convert(request: PandocConvertRequest) -> RenderResponse:
    ts = int(time.monotonic() * 1000)
    input_path = WORKSPACE / f".pandoc_in_{os.getpid()}_{ts}.md"
    output_path = WORKSPACE / f".pandoc_out_{os.getpid()}_{ts}"
    ref_path: Path | None = None

    try:
        input_path.write_text(request.input_content, encoding="utf-8")

        cmd = [
            "pandoc",
            str(input_path),
            "-f",
            request.reader,
            "-t",
            request.writer,
            "-o",
            str(output_path),
        ]

        if request.reference_doc_b64:
            ref_path = WORKSPACE / f".pandoc_ref_{os.getpid()}_{ts}.docx"
            ref_path.write_bytes(base64.b64decode(request.reference_doc_b64))
            cmd.extend(["--reference-doc", str(ref_path)])

        for arg in request.extra_args:
            cmd.append(arg)

        logger.info("执行 pandoc: %s", " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=request.timeout_seconds
        )

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace") if stderr else "未知错误"
            return RenderResponse(
                success=False,
                error=f"pandoc失败 (exit {proc.returncode}): {error_msg}",
            )

        if not output_path.exists():
            return RenderResponse(success=False, error="pandoc输出文件未生成")

        content = output_path.read_bytes()
        return RenderResponse(
            success=True,
            content_b64=base64.b64encode(content).decode("ascii"),
            size_bytes=len(content),
        )
    except TimeoutError:
        return RenderResponse(success=False, error="pandoc超时")
    except Exception as e:
        logger.exception("pandoc转换失败: %s", e)
        return RenderResponse(success=False, error=str(e))
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        if ref_path is not None:
            ref_path.unlink(missing_ok=True)


# ───────────────────── 浏览器管理 ─────────────────────


class BrowserManager:
    """Playwright Chromium 单例管理."""

    def __init__(self) -> None:
        self._browser: Any | None = None
        self._playwright_manager: Any | None = None
        self._lock = asyncio.Lock()

    async def get_browser(self) -> Any:
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            if self._browser is not None:
                with contextlib.suppress(Exception):
                    await self._browser.close()
                self._browser = None
            if self._playwright_manager is not None:
                with contextlib.suppress(Exception):
                    await self._playwright_manager.stop()
                self._playwright_manager = None

            from playwright.async_api import async_playwright

            self._playwright_manager = await async_playwright().start()
            try:
                self._browser = await self._playwright_manager.chromium.launch(
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
            except Exception:
                await self._playwright_manager.stop()
                self._playwright_manager = None
                raise
            return self._browser


async def _block_external_request(route: Any) -> None:
    url = route.request.url
    if url.startswith(("about:", "blob:", "data:")):
        await route.continue_()
        return
    await route.abort()


_browser_manager = BrowserManager()


# ───────────────────── 辅助函数 ─────────────────────


async def _playwright_render_png(
    *,
    html_content: str,
    selector: str,
    viewport_width: int,
    viewport_height: int,
    scale: int,
    timeout_seconds: float,
) -> bytes:
    """Playwright 渲染 HTML → PNG bytes (公共渲染逻辑).

    set_content 加载 HTML, 等待 window.__rendered 标志, 截图 selector 元素.

    Raises:
        RuntimeError: sentinel 报错或输出文件未生成
    """
    output_path = WORKSPACE / f".render_{os.getpid()}_{int(time.monotonic() * 1000)}.png"
    try:
        browser = await _browser_manager.get_browser()
        context = await browser.new_context(
            viewport={"width": viewport_width, "height": viewport_height},
            device_scale_factor=scale,
        )
        try:
            page = await context.new_page()
            await page.set_content(html_content, wait_until="load")

            await page.wait_for_function(
                "() => window.__rendered === true || window.__renderError",
                timeout=int(timeout_seconds * 1000),
            )

            error = await page.evaluate("window.__renderError")
            if error:
                raise RuntimeError(f"渲染失败: {error}")

            await page.locator(selector).screenshot(path=str(output_path))
        finally:
            await context.close()

        if not output_path.exists():
            raise RuntimeError("PNG文件未生成")
        return output_path.read_bytes()
    finally:
        output_path.unlink(missing_ok=True)


def _clean_output_dir() -> None:
    if OUTPUT_DIR.exists():
        for item in OUTPUT_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
    else:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _collect_outputs() -> list[CreatedFile]:
    files: list[CreatedFile] = []
    if not OUTPUT_DIR.exists():
        return files
    for path in sorted(OUTPUT_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            content = path.read_bytes()
            files.append(
                CreatedFile(
                    filename=path.name,
                    relative_path=str(path.relative_to(OUTPUT_DIR)),
                    size_bytes=len(content),
                    content_b64=base64.b64encode(content).decode("ascii"),
                )
            )
        except Exception:
            continue
    return files


def _truncate(value: str, max_chars: int) -> tuple[str, bool]:
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars], True
