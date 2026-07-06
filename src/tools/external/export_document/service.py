"""文档导出服务层 - 核心渲染和文件管理逻辑.

渲染路径:
- PDF: GFM预处理 → pandoc → HTML → CSS模板 → Chromium → PDF
- DOCX: GFM预处理 → pandoc → DOCX (reference-doc 样式)

核心能力:
- GFM 预处理: mermaid/vega-lite 渲染为 PNG, raw HTML/callout 转为 Markdown
- pandoc 命令行调用 (GFM → HTML/DOCX)
- Chromium 浏览器单例 + PDF 渲染 (HTML → PDF, 支持模板CSS)
- 文件管理 (下载 token,附件注册,去重,配额)
- GFM 结构化解析 (目录) + 文档摘要自动生成
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
import yaml

from src.config.runtime_env import get_tool_runtime_base_url
from src.files.paths import FILES_EXPORTS
from src.tools.shared.browser_renderer import get_browser_renderer
from src.tools.shared.file_output import (
    build_unique_filename as _build_unique_filename,
)
from src.tools.shared.file_output import (
    register_tool_output,
)
from src.tools.shared.file_output import (
    validate_filename as _validate_filename,
)

from .css_generator import build_html_document, generate_css
from .gfm_parser import parse_gfm_structure
from .gfm_preprocessor import preprocess_gfm
from .summary_generator import (
    extract_auto_summary,
    schedule_summary_generation,
)

logger = logging.getLogger(__name__)

# === pandoc 配置 ===
# gfm reader + pandoc 扩展, 补全 GFM 规范所需特性
_PANDOC_READER = (
    "gfm"
    "+tex_math_dollars"
    "+raw_html"
    "+fenced_divs"
    "+pipe_tables"
    "+smart"
    "+definition_lists"
    "+attributes"
)
_PANDOC_WRITERS = {
    "html": "html5",
    "docx": "docx",
}

# === 模板目录 ===
_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _get_reference_doc(style: str) -> Path | None:
    """查找 style 对应的 DOCX reference-doc, 不存在则回退到 default.docx.

    Args:
        style: 模板名称 (default/academic/business/technical)

    Returns:
        reference-doc 文件路径, 都不存在时返回 None
    """
    styled = _TEMPLATES_DIR / f"{style}.docx"
    if styled.exists():
        return styled
    default = _TEMPLATES_DIR / "default.docx"
    return default if default.exists() else None


# ───────────────────── 模板加载 ─────────────────────


def load_template_config(style_name: str) -> dict[str, Any]:
    """加载 YAML 模板配置.

    Args:
        style_name: 模板名称, 如 'default', 'academic', 'business' 等.
            找不到时回退到 'default'.

    Returns:
        模板配置字典.
    """
    yaml_path = _TEMPLATES_DIR / f"{style_name}.yaml"
    if not yaml_path.exists():
        if style_name != "default":
            logger.warning("模板 '%s' 不存在, 回退到 default", style_name)
            yaml_path = _TEMPLATES_DIR / "default.yaml"
        if not yaml_path.exists():
            return {}

    with Path(yaml_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ───────────────────── 主入口 ─────────────────────


async def run_export_document(
    content: str,
    style: str,
    output_format: str,
    filename: str,
    *,
    user_id: str,
    thread_id: str,
) -> dict[str, Any]:
    """执行文档导出: GFM预处理 → pandoc渲染 → 文件注册.

    Returns:
        包含 file_id, file_url, filename, format, size_bytes 的结果字典.
        失败时包含 error 字段.
    """
    try:
        _validate_filename(filename)

        from src.core.path_resolver import get_user_path_resolver

        resolver = get_user_path_resolver()
        export_dir = resolver.get_shared_storage_path(user_id, thread_id, FILES_EXPORTS)

        output_filename, display_filename = _build_unique_filename(
            filename, output_format
        )
        output_path = export_dir / output_filename

        render_charts = output_format in {"pdf", "docx"}
        processed = await preprocess_gfm(
            content, export_dir, render_charts=render_charts
        )

        if output_format == "docx":
            await _render_docx(processed, output_path, export_dir, style)
        else:
            await _render_pdf(processed, output_path, export_dir, style)

        if not output_path.exists():
            raise RuntimeError(f"转换失败, 输出文件未生成: {display_filename}")

        doc_structure = parse_gfm_structure(content, output_format)

        # 确定性提取摘要 (后台 LLM 再覆盖优化)
        summary = extract_auto_summary(content)

        doc_structure.summary = summary or ""
        document_meta_json = json.dumps(
            doc_structure.to_json_dict(), ensure_ascii=False
        )

        result = await register_tool_output(
            output_path=output_path,
            display_filename=display_filename,
            output_filename=output_filename,
            output_format=output_format,
            file_type="document",
            content=content,
            summary=summary,
            user_id=user_id,
            thread_id=thread_id,
            document_meta=document_meta_json,
        )

        if result.get("success"):
            from src.core.context import get_user_context_or_none

            ctx = get_user_context_or_none()
            schedule_summary_generation(
                file_id=result["file_id"],
                gfm_content=content,
                user_id=user_id,
                thread_id=thread_id,
                agent_id=ctx.agent_id if ctx else "unknown",
            )

        return result

    except Exception as e:
        logger.exception("run_export_document 失败: %s", e)
        return {"success": False, "error": str(e), "message": f"文档导出失败: {e}"}


# ───────────────────── 渲染路径 ─────────────────────


async def _render_pdf(
    content: str,
    output_path: Path,
    export_dir: Path,
    style: str,
) -> None:
    """PDF 渲染: markdown → pandoc → HTML → 模板CSS → Chromium PDF."""
    template_config = load_template_config(style)
    css = generate_css(template_config)

    source_path = _write_temp_source(content, export_dir)
    html_path = export_dir / f"_tmp_{os.urandom(8).hex()}.html"

    try:
        await _run_pandoc(source_path, html_path, "html")
        html_body = html_path.read_text(encoding="utf-8")
        styled_html = build_html_document(html_body, css)
        await get_browser_renderer().render_to_pdf(styled_html, output_path)
    finally:
        for p in (source_path, html_path):
            if p.exists():
                p.unlink(missing_ok=True)


async def _render_docx(
    content: str,
    output_path: Path,
    export_dir: Path,
    style: str,
) -> None:
    """DOCX 渲染: markdown → pandoc → DOCX (reference-doc 样式)."""
    source_path = _write_temp_source(content, export_dir)
    ref_doc = _get_reference_doc(style)
    try:
        await _run_pandoc(source_path, output_path, "docx", reference_doc=ref_doc)
    finally:
        if source_path.exists():
            source_path.unlink(missing_ok=True)


# ───────────────────── pandoc 调用 ─────────────────────

_TOOL_RUNTIME_BASE_URL: str | None = None


def _get_tool_runtime_url() -> str:
    global _TOOL_RUNTIME_BASE_URL
    if _TOOL_RUNTIME_BASE_URL is None:
        _TOOL_RUNTIME_BASE_URL = get_tool_runtime_base_url()
    return _TOOL_RUNTIME_BASE_URL


async def _run_pandoc(
    input_path: Path,
    output_path: Path,
    output_format: str,
    reference_doc: Path | None = None,
) -> None:
    """通过 tool-runtime 容器调用 pandoc 执行格式转换.

    Args:
        input_path: 输入 markdown 文件路径
        output_path: 输出文件路径
        output_format: 输出格式标识 (html/docx)
        reference_doc: DOCX reference-doc 路径, 仅 docx 格式有效
    """
    input_content = await asyncio.to_thread(input_path.read_text, encoding="utf-8")
    writer = _PANDOC_WRITERS[output_format]

    extra_args: list[str] = []
    if output_format == "html":
        extra_args.extend(["--standalone", "--mathml"])

    reference_doc_b64: str | None = None
    if (
        output_format == "docx"
        and reference_doc
        and await asyncio.to_thread(reference_doc.exists)
    ):
        ref_bytes = await asyncio.to_thread(reference_doc.read_bytes)
        reference_doc_b64 = base64.b64encode(ref_bytes).decode("ascii")

    payload = {
        "input_content": input_content,
        "reader": _PANDOC_READER,
        "writer": writer,
        "extra_args": extra_args,
        "timeout_seconds": 60.0,
    }
    if reference_doc_b64:
        payload["reference_doc_b64"] = reference_doc_b64

    base_url = _get_tool_runtime_url()
    logger.info("调用 tool-runtime pandoc: %s → %s", input_path.name, output_format)

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(70.0, connect=5.0),
    ) as client:
        response = await client.post("/convert/pandoc", json=payload)
        response.raise_for_status()
        data = response.json()

    if not data.get("success"):
        raise RuntimeError(
            f"pandoc 转换失败: {data.get('error', '未知错误')}",
        )

    content = base64.b64decode(data["content_b64"])
    await asyncio.to_thread(output_path.write_bytes, content)


def _write_temp_source(content: str, export_dir: Path) -> Path:
    """将 markdown 内容写入临时文件."""
    tmp_path = export_dir / f"_tmp_{os.urandom(8).hex()}.md"
    tmp_path.write_text(content, encoding="utf-8")
    return tmp_path
