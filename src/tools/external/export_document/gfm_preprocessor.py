"""GFM 预处理器 - 在 pandoc 之前将 pandoc 无法渲染的内容转换为可渲染形式.

处理流程:
1. mermaid/vega-lite/markmap 代码块 → 预渲染 PNG → 替换为 ![](path) 引用
2. raw HTML 块 → 转为等价 Markdown (保留语义)
3. fenced divs / callout (:::) → 转为引用块 (>)
4. SVG 图片引用 → 暂保留 (需文件系统访问, 后续扩展)

设计约定:
- 不修改 pandoc 能正常处理的 GFM 内容
- 所有转换保持语义等价, 不丢失信息
- 图表渲染通过 tool-runtime `/render/chart` 端点 (HTML 构建下沉 tool-runtime)
- 异步: 图表渲染是 IO 密集型操作
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

_CODE_FENCE_RE = re.compile(
    r"^```(\w[\w\-]*)\s*\n(.*?)^```",
    re.MULTILINE | re.DOTALL,
)

_CHART_LANGS = {"mermaid", "vega-lite", "vega_lite", "markmap"}

_LANG_ENGINE_MAP = {
    "mermaid": "mermaid",
    "vega-lite": "vega_lite",
    "vega_lite": "vega_lite",
    "markmap": "markmap",
}

_CALLOUT_TYPES = {
    "note": "NOTE",
    "tip": "TIP",
    "warning": "WARNING",
    "caution": "CAUTION",
    "important": "IMPORTANT",
    "info": "INFO",
}


async def preprocess_gfm(
    content: str,
    export_dir: Path,
    *,
    render_charts: bool = True,
) -> str:
    """预处理 GFM 源码, 使其可被 pandoc 完整渲染.

    Args:
        content: 原始 GFM 源码
        export_dir: 导出目录, 图表 PNG 会写入此目录
        render_charts: 是否渲染图表 (False 时仅做文本转换)

    Returns:
        预处理后的 GFM 源码
    """
    content = _convert_callout_divs(content)
    content = _convert_raw_html(content)

    if render_charts:
        content = await _render_chart_blocks(content, export_dir)

    return content


async def _render_chart_blocks(content: str, export_dir: Path) -> str:
    """检测图表代码块, 预渲染 PNG, 替换为图片引用."""
    chart_blocks = list(_find_chart_blocks(content))
    if not chart_blocks:
        return content

    charts_dir = export_dir / "_charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    replacements: list[tuple[int, int, str]] = []

    for idx, (lang, code, start, end) in enumerate(chart_blocks):
        engine = _LANG_ENGINE_MAP.get(lang)
        if not engine:
            continue

        try:
            png_path = await _render_single_chart(
                engine=engine,
                code=code,
                output_path=charts_dir / f"chart_{idx}.png",
            )
            relative = png_path.relative_to(export_dir)
            replacements.append((start, end, f"![chart_{idx}]({relative})"))
            logger.info("图表预渲染完成: %s (%s)", lang, png_path.name)
        except Exception as e:
            logger.warning("图表预渲染失败 (%s): %s, 保留原始代码块", lang, e)

    if not replacements:
        return content

    parts: list[str] = []
    prev = 0
    for start, end, replacement in replacements:
        parts.append(content[prev:start])
        parts.append(replacement)
        prev = end
    parts.append(content[prev:])

    return "".join(parts)


def _find_chart_blocks(content: str) -> Iterator[tuple[str, str, int, int]]:
    """查找所有图表类型的围栏代码块.

    Yields:
        (lang, code, start_offset, end_offset)
    """
    for m in _CODE_FENCE_RE.finditer(content):
        lang = m.group(1).lower()
        if lang in _CHART_LANGS:
            yield lang, m.group(2), m.start(), m.end()


async def _render_single_chart(
    engine: str,
    code: str,
    output_path: Path,
) -> Path:
    """渲染单个图表为 PNG (通过 tool-runtime /render/chart).

    HTML 构建在 tool-runtime 内完成, app 端无需加载 JS 库.
    """
    from src.tools.shared.browser_renderer import get_browser_renderer

    renderer = get_browser_renderer()
    await renderer.render_chart(
        engine=engine,
        code=code,
        title=None,
        width=None,
        height=None,
        scale=3,
        output_path=output_path,
    )

    if not await asyncio.to_thread(output_path.exists):
        raise RuntimeError(f"图表渲染失败: {output_path}")

    return output_path


def _convert_callout_divs(content: str) -> str:
    """将 fenced divs / callout 语法转为引用块.

    支持的格式:
    - ::: tip\n内容\n:::            → > **TIP**\n> 内容
    - ::: {.tip .important}\n:::    → 同上
    - ::: note\n内容\n:::           → > **NOTE**\n> 内容
    - ::: 任意文字\n内容\n:::       → > 任意文字\n> 内容
    """
    lines = content.split("\n")
    result: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        div_match = re.match(r"^:{3,}\s*(.*)", line)

        if div_match:
            label = div_match.group(1).strip()
            label = _parse_div_label(label)

            block_lines: list[str] = []
            depth = 1
            i += 1

            while i < len(lines) and depth > 0:
                inner = lines[i]
                if re.match(r"^:{3,}\s*$", inner):
                    depth -= 1
                    if depth > 0:
                        block_lines.append(inner)
                elif re.match(r"^:{3,}\s*", inner):
                    depth += 1
                    block_lines.append(inner)
                else:
                    block_lines.append(inner)
                i += 1

            if label:
                result.append(f"> **{label}**")
            for bl in block_lines:
                stripped = bl.strip()
                if stripped:
                    result.append(f"> {stripped}")
                else:
                    result.append(">")
            result.append("")
            continue

        result.append(line)
        i += 1

    return "\n".join(result)


def _parse_div_label(raw: str) -> str:
    """解析 fenced div 标签, 提取 callout 类型.

    Examples:
        "tip" → "TIP"
        "{.warning .important}" → "WARNING"
        "自定义标题" → "自定义标题"
        "" → ""
    """
    if not raw:
        return ""

    class_match = re.findall(r"\.(\w+)", raw)
    if class_match:
        for cls in class_match:
            if cls.lower() in _CALLOUT_TYPES:
                return _CALLOUT_TYPES[cls.lower()]
        return class_match[0].upper()

    if raw.lower() in _CALLOUT_TYPES:
        return _CALLOUT_TYPES[raw.lower()]

    return raw


def _convert_raw_html(content: str) -> str:
    """将 raw HTML 块转为等价 Markdown.

    仅处理常见语义 HTML 标签, 不做完整 HTML 解析.
    处理的标签:
    - <details>/<summary> → 引用块
    - <mark> → **粗体**
    - <sub>/<sup> → 保留文本 (Markdown 无下标/上标语法)
    - <br>/<hr> → 保留 (Markdown 有对应语法)
    """
    content = re.sub(
        r"<details>\s*<summary>(.*?)</summary>\s*(.*?)\s*</details>",
        _replace_details,
        content,
        flags=re.DOTALL,
    )

    content = re.sub(r"<mark>(.*?)</mark>", r"**\1**", content)
    content = re.sub(r"<sub>(.*?)</sub>", r"\1", content)
    content = re.sub(r"<sup>(.*?)</sup>", r"\1", content)
    content = re.sub(r"<br\s*/?>", "  \n", content)
    return re.sub(r"^<hr\s*/?>$", "---", content, flags=re.MULTILINE)


def _replace_details(m: re.Match) -> str:
    """将 <details><summary> 转为引用块."""
    summary = m.group(1).strip()
    body = m.group(2).strip()

    lines = [f"> **{summary}**"]
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped:
            lines.append(f"> {stripped}")
        else:
            lines.append(">")
    return "\n".join(lines)
