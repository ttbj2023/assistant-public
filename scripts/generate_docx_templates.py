"""DOCX reference-doc 模板生成器.

从 pandoc 默认 reference.docx 出发, 按 YAML 样式配置修改 Word 样式定义,
生成 4 个风格各异的 pandoc reference-doc 文件.

用法:
    python scripts/generate_docx_templates.py

前置条件:
    pip install python-docx    # dev 依赖, pyproject.toml 已声明

输出:
    src/tools/external/export_document/templates/default.docx
    src/tools/external/export_document/templates/academic.docx
    src/tools/external/export_document/templates/business.docx
    src/tools/external/export_document/templates/technical.docx

设计要点:
    1. 起点: pandoc --print-default-data-file reference.docx (已存在于 templates/default.docx)
    2. python-docx 能改字体/字号/颜色/间距/页边距, 但不能设置 CJK 字体
    3. 通过 oxml (qn('w:eastAsia')) 补全 CJK 字体设置
    4. 补全 pandoc 默认 ref 缺失的 Source Code 段落样式
    5. 补全页边距 (默认 ref 无 sectPr pgMar)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

if TYPE_CHECKING:
    from lxml.etree import _Element as lxml_Element

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / (
    "src/tools/external/export_document/templates"
)

STYLES = ["default", "academic", "business", "technical"]

# 每个风格的 DOCX 字体映射 (YAML 的 CSS font-stack 无法直接用于 DOCX, 需显式指定)
# 键: style_name -> (latin_font, cjk_font, heading_latin, heading_cjk)
DOCX_FONTS: dict[str, tuple[str, str, str, str]] = {
    "default": ("Arial", "Microsoft YaHei", "Arial", "Microsoft YaHei"),
    "academic": ("Times New Roman", "SimSun", "Arial", "SimHei"),
    "business": ("Arial", "Microsoft YaHei", "Arial", "Microsoft YaHei"),
    "technical": ("Segoe UI", "Microsoft YaHei", "Segoe UI", "Microsoft YaHei"),
}

# 代码块字体 (跨风格统一)
CODE_FONT = "Consolas"


# ───────────────────── YAML 辅助 ─────────────────────


def load_yaml(style: str) -> dict:
    """加载 YAML 模板配置."""
    path = TEMPLATES_DIR / f"{style}.yaml"
    if not path.exists():
        print(f"  [警告] {style}.yaml 不存在, 跳过")
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_length_pt(value: str | float, base_pt: float = 12.0) -> float:
    """将 CSS 长度字符串转为 pt 数值.

    Args:
        value: CSS 长度 (如 "18mm", "2.54cm", "2em") 或数值 (已是 pt)
        base_pt: em 单位的基准字号 (默认 12pt)

    Returns:
        pt 数值
    """
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    # em -> pt (基于正文字号)
    if text.endswith("em"):
        return float(text[:-2]) * base_pt
    # mm -> pt (1mm = 2.834645669pt)
    if text.endswith("mm"):
        return float(text[:-2]) * 2.834645669
    # cm -> pt
    if text.endswith("cm"):
        return float(text[:-2]) * 28.34645669
    # in -> pt
    if text.endswith("in"):
        return float(text[:-2]) * 72.0
    # px -> pt (96dpi)
    if text.endswith("px"):
        return float(text[:-2]) * 0.75
    # pt
    if text.endswith("pt"):
        return float(text[:-2])
    return float(text)


def parse_margin_mm(value: str) -> float:
    """将 CSS 长度字符串转为 mm 数值 (用于页边距)."""
    text = str(value).strip().lower()
    if text.endswith("mm"):
        return float(text[:-2])
    if text.endswith("cm"):
        return float(text[:-2]) * 10.0
    if text.endswith("in"):
        return float(text[:-2]) * 25.4
    return float(text)


def hex_to_rgb(hex_str: str) -> RGBColor:
    """将 '#RRGGBB' 或 '#RGB' 转为 RGBColor."""
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return RGBColor.from_string(h)


# ───────────────────── oxml CJK 字体辅助 ─────────────────────


def set_cjk_fonts(
    rpr_element: lxml_Element,
    latin: str,
    cjk: str,
) -> None:
    """在 rPr 元素上同时设置西文字体和 CJK 字体.

    python-docx 的 font.name 只设置 w:ascii + w:hAnsi,
    无法设置 w:eastAsia (CJK 字体), 必须通过 oxml 操作.
    """
    rfonts = rpr_element.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr_element.makeelement(qn("w:rFonts"), {})
        rpr_element.insert(0, rfonts)
    rfonts.set(qn("w:ascii"), latin)
    rfonts.set(qn("w:hAnsi"), latin)
    rfonts.set(qn("w:eastAsia"), cjk)
    rfonts.set(qn("w:cs"), latin)


def set_doc_defaults_cjk(doc: Document, latin: str, cjk: str) -> None:
    """设置 docDefaults 的全局默认字体 (含 CJK)."""
    styles_el = doc.styles.element
    rpr_def = styles_el.find(qn("w:docDefaults") + "/" + qn("w:rPrDefault"))
    if rpr_def is None:
        return
    rpr = rpr_def.find(qn("w:rPr"))
    if rpr is None:
        rpr = rpr_def.makeelement(qn("w:rPr"), {})
        rpr_def.append(rpr)
    set_cjk_fonts(rpr, latin, cjk)


# ───────────────────── 样式修改 ─────────────────────


def configure_body_styles(
    doc: Document,
    config: dict,
    latin: str,
    cjk: str,
) -> None:
    """配置正文相关样式 (Normal, Body Text, First Paragraph, Compact)."""
    sizes = config.get("font_sizes", {})
    spacing = config.get("spacing", {})

    body_size = Pt(sizes.get("body", 12))
    line_height = spacing.get("line_height", 1.5)
    para_after = spacing.get("paragraph_after", 0)
    first_indent_em = spacing.get("first_line_indent")

    for style_name in ("Normal", "Body Text", "First Paragraph"):
        style = _get_style_safe(doc, style_name)
        if style is None:
            continue
        style.font.size = body_size
        style.font.name = latin
        # CJK 字体
        rpr = style.element.get_or_add_rPr()
        set_cjk_fonts(rpr, latin, cjk)

        pf = style.paragraph_format
        pf.line_spacing = float(line_height)
        if para_after:
            pf.space_after = Pt(float(para_after))
        if first_indent_em:
            em_val = float(re.sub(r"[^\d.]", "", str(first_indent_em)))
            pf.first_line_indent = Pt(em_val * sizes.get("body", 12))


def configure_heading_styles(doc: Document, config: dict, latin: str, cjk: str) -> None:
    """配置标题样式 (Heading 1-6)."""
    sizes = config.get("font_sizes", {})
    colors = config.get("colors", {})
    headings_cfg = config.get("headings", {})
    heading_color = colors.get("heading", "#000000")

    size_map = {
        1: sizes.get("h1", 22),
        2: sizes.get("h2", 18),
        3: sizes.get("h3", 15),
        4: sizes.get("h4", 13),
        5: sizes.get("h5", 12),
        6: sizes.get("h6", 12),
    }

    # 各风格的标题颜色覆盖
    for level in range(1, 7):
        style = _get_style_safe(doc, f"Heading {level}")
        if style is None:
            continue

        style.font.size = Pt(size_map[level])
        style.font.bold = True
        style.font.name = latin
        rpr = style.element.get_or_add_rPr()
        set_cjk_fonts(rpr, latin, cjk)

        # 颜色: 优先取 headings.h{n}.color, 再取全局 heading 色
        h_cfg = headings_cfg.get(f"h{level}", {})
        color_str = h_cfg.get("color", heading_color)
        style.font.color.rgb = hex_to_rgb(color_str)

        pf = style.paragraph_format
        pf.space_before = Pt(14 if level <= 2 else 10)
        pf.space_after = Pt(6 if level <= 2 else 4)
        pf.keep_with_next = True

        # 学术模板 H1 居中
        if h_cfg.get("align") == "center":
            pf.alignment = WD_ALIGN_PARAGRAPH.CENTER


def configure_block_text(doc: Document, config: dict) -> None:
    """配置引用块样式 (Block Text)."""
    blockquotes = config.get("blockquotes", {})
    style = _get_style_safe(doc, "Block Text")
    if style is None:
        return

    pf = style.paragraph_format
    pf.left_indent = Pt(18)
    pf.right_indent = Pt(18)
    pf.space_before = Pt(6)
    pf.space_after = Pt(6)

    color_str = blockquotes.get("color", "#4b5563")
    style.font.color.rgb = hex_to_rgb(color_str)
    style.font.italic = True


def configure_code_styles(doc: Document, config: dict, latin: str, cjk: str) -> None:
    """配置代码样式 (Verbatim Char + Source Code).

    pandoc 默认 ref 没有 Source Code 段落样式, 需要新增.
    """
    code_cfg = config.get("code_blocks", {})
    code_size = Pt(code_cfg.get("font_size", 10.5))
    code_bg = code_cfg.get("background", "#f6f8fa").lstrip("#")

    # Verbatim Char (行内代码)
    verbatim = _get_style_safe(doc, "Verbatim Char")
    if verbatim:
        verbatim.font.name = CODE_FONT
        verbatim.font.size = code_size
        rpr = verbatim.element.get_or_add_rPr()
        set_cjk_fonts(rpr, CODE_FONT, cjk)

    # Source Code (代码块) — pandoc 期望但不一定存在于默认 ref
    source_code = _get_style_safe(doc, "Source Code")
    if source_code is None:
        source_code = doc.styles.add_style("Source Code", WD_STYLE_TYPE.PARAGRAPH)
        source_code.base_style = doc.styles["Normal"]

    source_code.font.name = CODE_FONT
    source_code.font.size = code_size
    rpr = source_code.element.get_or_add_rPr()
    set_cjk_fonts(rpr, CODE_FONT, cjk)

    pf = source_code.paragraph_format
    pf.space_before = Pt(6)
    pf.space_after = Pt(6)
    pf.line_spacing = 1.4

    # 背景色 (shading)
    _set_shading(source_code.element, code_bg)


def configure_table_style(doc: Document, config: dict) -> None:
    """配置表格样式 (Table)."""
    tables_cfg = config.get("tables", {})
    style = _get_style_safe(doc, "Table")
    if style is None:
        return

    border_color = tables_cfg.get("border_color", "#d0d7de").lstrip("#")
    _set_table_borders(style.element, border_color)


def configure_page_margins(doc: Document, config: dict) -> None:
    """配置页边距和页面大小."""
    margins = config.get("margins", {})
    if not margins:
        return

    for section in doc.sections:
        section.page_width = Mm(210)
        section.page_height = Mm(297)
        section.top_margin = Mm(parse_margin_mm(margins.get("top", "20mm")))
        section.bottom_margin = Mm(parse_margin_mm(margins.get("bottom", "20mm")))
        section.left_margin = Mm(parse_margin_mm(margins.get("left", "22mm")))
        section.right_margin = Mm(parse_margin_mm(margins.get("right", "22mm")))


# ───────────────────── 低级 XML 辅助 ─────────────────────


def _get_style_safe(doc: Document, name: str) -> Any:
    """安全获取样式, 不存在返回 None."""
    try:
        return doc.styles[name]
    except KeyError:
        return None


def _set_shading(style_element: lxml_Element, fill_hex: str) -> None:
    """为样式元素设置背景色 (w:shd)."""
    ppr = style_element.find(qn("w:pPr"))
    if ppr is None:
        ppr = style_element.makeelement(qn("w:pPr"), {})
        style_element.insert(0, ppr)

    # 移除已有 shd
    for existing in ppr.findall(qn("w:shd")):
        ppr.remove(existing)

    shd = ppr.makeelement(qn("w:shd"), {})
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill_hex)
    ppr.append(shd)


def _set_table_borders(style_element: lxml_Element, color_hex: str) -> None:
    """为表格样式设置边框."""
    ppr = style_element.find(qn("w:pPr"))
    if ppr is None:
        return

    tblpr = ppr.find(qn("w:tblPr"))
    if tblpr is None:
        return

    borders = tblpr.find(qn("w:tblBorders"))
    if borders is None:
        borders = tblpr.makeelement(qn("w:tblBorders"), {})
        tblpr.append(borders)

    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        existing = borders.find(qn(f"w:{edge}"))
        if existing is not None:
            borders.remove(existing)
        border = borders.makeelement(qn(f"w:{edge}"), {})
        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), "4")
        border.set(qn("w:space"), "0")
        border.set(qn("w:color"), color_hex)
        borders.append(border)


# ───────────────────── 主流程 ─────────────────────


def generate_template(style: str) -> Path | None:
    """生成单个风格的 DOCX reference-doc.

    Args:
        style: 风格名称 (default/academic/business/technical)

    Returns:
        输出文件路径, 失败返回 None
    """
    config = load_yaml(style)
    if not config:
        return None

    latin, cjk, heading_latin, heading_cjk = DOCX_FONTS[style]

    # 从 pandoc 默认 reference.docx 加载
    base_docx = TEMPLATES_DIR / "default.docx"
    doc = Document(str(base_docx))

    # 1. 全局默认字体 (含 CJK)
    set_doc_defaults_cjk(doc, latin, cjk)

    # 2. 正文样式
    configure_body_styles(doc, config, latin, cjk)

    # 3. 标题样式
    configure_heading_styles(doc, config, heading_latin, heading_cjk)

    # 4. 引用块
    configure_block_text(doc, config)

    # 5. 代码样式
    configure_code_styles(doc, config, latin, cjk)

    # 6. 表格样式
    configure_table_style(doc, config)

    # 7. 页边距
    configure_page_margins(doc, config)

    # 8. Title 样式 (文档标题)
    title_style = _get_style_safe(doc, "Title")
    if title_style:
        title_style.font.name = heading_latin
        title_style.font.bold = True
        rpr = title_style.element.get_or_add_rPr()
        set_cjk_fonts(rpr, heading_latin, heading_cjk)

    output_path = TEMPLATES_DIR / f"{style}.docx"
    doc.save(str(output_path))
    return output_path


def generate_default_template() -> Path:
    """重新生成 default.docx (从 pandoc 原始 ref 开始).

    default.docx 是其他模板的基础, 需要先重置为 pandoc 默认再添加通用增强.
    """
    # 重新提取 pandoc 默认 reference.docx
    import subprocess

    result = subprocess.run(
        ["pandoc", "--print-default-data-file", "reference.docx"],
        capture_output=True,
        check=True,
    )
    base_path = TEMPLATES_DIR / "default.docx"
    base_path.write_bytes(result.stdout)

    # 然后像其他模板一样增强
    config = load_yaml("default")
    latin, cjk, heading_latin, heading_cjk = DOCX_FONTS["default"]

    doc = Document(str(base_path))
    set_doc_defaults_cjk(doc, latin, cjk)
    configure_body_styles(doc, config, latin, cjk)
    configure_heading_styles(doc, config, heading_latin, heading_cjk)
    configure_block_text(doc, config)
    configure_code_styles(doc, config, latin, cjk)
    configure_table_style(doc, config)
    configure_page_margins(doc, config)

    title_style = _get_style_safe(doc, "Title")
    if title_style:
        title_style.font.name = heading_latin
        title_style.font.bold = True
        rpr = title_style.element.get_or_add_rPr()
        set_cjk_fonts(rpr, heading_latin, heading_cjk)

    doc.save(str(base_path))
    return base_path


def main() -> None:
    """生成所有 DOCX reference-doc 模板."""
    print("=" * 60)
    print("DOCX reference-doc 模板生成器")
    print("=" * 60)

    # 先重置 default.docx (其他模板的基础)
    print("\n[1/5] 重置 default.docx (pandoc 原始 reference.docx)...")
    default_path = generate_default_template()
    print(f"  -> {default_path.name} ({default_path.stat().st_size:,} bytes)")

    # 生成其余 3 个模板
    for i, style in enumerate(["academic", "business", "technical"], start=2):
        print(f"\n[{i}/5] 生成 {style}.docx...")
        path = generate_template(style)
        if path:
            print(f"  -> {path.name} ({path.stat().st_size:,} bytes)")
        else:
            print(f"  [失败] {style}.yaml 配置缺失")

    print("\n" + "=" * 60)
    print("生成完成!")
    print(f"  输出目录: {TEMPLATES_DIR}")
    print("\n验证方法:")
    print("  pandoc test.md --reference-doc academic.docx -o test.docx")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
