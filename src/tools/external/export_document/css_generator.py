"""YAML 模板配置 → CSS 字符串生成器.

参考 claude-doc-processor 的 WordHTMLGenerator._generate_styles() 简化而来,
适配 Chromium PDF 渲染场景(HTML5, 现代 CSS).
"""

from __future__ import annotations

from typing import Any


def generate_css(config: dict[str, Any]) -> str:
    """将模板配置字典转换为完整 CSS 字符串.

    Args:
        config: 从 YAML 模板加载的配置字典.

    Returns:
        用于嵌入 <style> 标签的 CSS 文本.
    """
    fonts = config.get("fonts", {})
    sizes = config.get("font_sizes", {})
    colors = config.get("colors", {})
    spacing = config.get("spacing", {})
    margins = config.get("margins", {})
    headings_cfg = config.get("headings", {})
    tables_cfg = config.get("tables", {})
    code_cfg = config.get("code_blocks", {})
    bq_cfg = config.get("blockquotes", {})
    pb_cfg = config.get("page_break", {})

    body_font = fonts.get("default", "Arial, sans-serif")
    heading_font = fonts.get("heading", body_font)
    code_font = fonts.get("code", "Consolas, monospace")
    body_size = sizes.get("body", 12)
    h_sizes = {
        1: sizes.get("h1", 22),
        2: sizes.get("h2", 18),
        3: sizes.get("h3", 15),
        4: sizes.get("h4", 13),
        5: sizes.get("h5", 12),
        6: sizes.get("h6", 12),
    }
    text_color = colors.get("text", "#1f2933")
    heading_color = colors.get("heading", "#1a1a2e")
    link_color = colors.get("link", "#2b6cb0")
    tbl_border = colors.get("table_border", "#d0d7de")
    tbl_header_bg = colors.get("table_header_bg", "#f0f4f8")
    tbl_zebra = colors.get("table_zebra", "#fafbfc")
    code_bg = colors.get("code_bg", "#f6f8fa")
    bq_bg = colors.get("blockquote_bg", "#f7fafc")
    bq_text = colors.get("blockquote_text", "#4b5563")

    line_height = spacing.get("line_height", 1.62)
    p_after = spacing.get("paragraph_after", 0.85)
    first_indent = spacing.get("first_line_indent", "0em")

    m_top = margins.get("top", "18mm")
    m_bottom = margins.get("bottom", "18mm")
    m_left = margins.get("left", "18mm")
    m_right = margins.get("right", "18mm")

    tbl_font_size = tables_cfg.get("font_size", 11)
    tbl_padding = tables_cfg.get("cell_padding", "8px 10px")
    tbl_header_color = tables_cfg.get("header_color", heading_color)

    code_font_size = code_cfg.get("font_size", 10.5)
    code_lh = code_cfg.get("line_height", 1.5)
    code_padding = code_cfg.get("padding", "12px 14px")

    # === 构建 CSS ===
    parts: list[str] = []

    # @page
    parts.append(
        f"@page {{ size: A4; margin: {m_top} {m_right} {m_bottom} {m_left}; }}"
    )

    # 全局
    parts.append("* { box-sizing: border-box; }")
    parts.append(
        f"body {{ color: {text_color}; font-family: {body_font}; "
        f"font-size: {body_size}pt; line-height: {line_height}; margin: 0; "
        f"-webkit-print-color-adjust: exact; print-color-adjust: exact; }}"
    )

    # 标题
    for level in range(1, 7):
        h_cfg = headings_cfg.get(f"h{level}", {})
        h_color = h_cfg.get("color", heading_color)
        h_size = h_sizes[level]
        h_align = h_cfg.get("align", "")
        h_bg = h_cfg.get("background", colors.get(f"h{level}_bg", ""))
        h_border_bottom = h_cfg.get("border_bottom", "")
        h_padding = h_cfg.get("padding", "")
        h_border_radius = h_cfg.get("border_radius", "")
        h_margin_top = h_cfg.get("margin_top", "1.2em" if level <= 2 else "0.8em")
        h_margin_bottom = h_cfg.get(
            "margin_bottom", "0.45em" if level <= 2 else "0.3em"
        )
        h_pb = h_cfg.get("padding_bottom", "")

        props = [
            f"font-family: {heading_font}",
            f"font-size: {h_size}pt",
            "font-weight: bold",
            f"color: {h_color}",
            "line-height: 1.3",
            f"margin: {h_margin_top} 0 {h_margin_bottom}",
        ]
        if h_align:
            props.append(f"text-align: {h_align}")
        if h_bg:
            props.append(f"background: {h_bg}")
        if h_border_bottom:
            props.append(f"border-bottom: {h_border_bottom}")
        if h_pb:
            props.append(f"padding-bottom: {h_pb}")
        if h_padding:
            props.append(f"padding: {h_padding}")
        if h_border_radius:
            props.append(f"border-radius: {h_border_radius}")
        # page-break-after: avoid for h1/h2
        if pb_cfg.get(f"h{level}_break_after") == "avoid":
            props.append("page-break-after: avoid")

        parts.append(f"h{level} {{ {'; '.join(props)}; }}")

    # 段落
    p_props = [f"margin: 0 0 {p_after}em"]
    if first_indent != "0em":
        p_props.append(f"text-indent: {first_indent}")
    parts.append(f"p {{ {'; '.join(p_props)}; }}")

    # 列表
    parts.append(
        "ul, ol { margin: 0 0 0.85em; padding-left: 1.8em; } "
        "li { margin-bottom: 0.3em; } "
        "li > ul, li > ol { margin-top: 0.2em; margin-bottom: 0; }"
    )

    # 表格
    tbl_break = (
        "page-break-inside: avoid;"
        if pb_cfg.get("table_break_inside") == "avoid"
        else ""
    )
    parts.append(
        f"table {{ border-collapse: collapse; width: 100%; margin: 0.85em 0; "
        f"font-size: {tbl_font_size}pt; {tbl_break} }} "
        f"th, td {{ border: 1px solid {tbl_border}; padding: {tbl_padding}; "
        f"text-align: left; vertical-align: top; }} "
        f"th {{ background: {tbl_header_bg}; font-weight: 700; color: {tbl_header_color}; }}"
    )
    if tables_cfg.get("zebra"):
        parts.append(f"tr:nth-child(even) {{ background: {tbl_zebra}; }}")

    # 代码块
    code_border = code_cfg.get("border", f"1px solid {tbl_border}")
    code_border_left = code_cfg.get("border_left", f"3px solid {link_color}")
    code_radius = code_cfg.get("border_radius", "")
    pre_break = (
        "page-break-inside: avoid;" if pb_cfg.get("pre_break_inside") == "avoid" else ""
    )
    pre_extras = f"border-radius: {code_radius};" if code_radius else ""
    parts.append(
        f"pre, code {{ font-family: {code_font}; }} "
        f"pre {{ background: {code_bg}; border: {code_border}; "
        f"border-left: {code_border_left}; padding: {code_padding}; "
        f"white-space: pre-wrap; word-wrap: break-word; margin: 0.85em 0; "
        f"font-size: {code_font_size}pt; line-height: {code_lh}; "
        f"{pre_break} {pre_extras} }} "
        f"code {{ background: {code_bg}; padding: 2px 5px; border-radius: 3px; font-size: 0.9em; }} "
        f"pre code {{ background: none; padding: 0; border-radius: 0; }}"
    )

    # 引用
    bq_break = (
        "page-break-inside: avoid;"
        if pb_cfg.get("blockquote_break_inside") == "avoid"
        else ""
    )
    bq_radius = bq_cfg.get("border_radius", "")
    bq_extras = f"border-radius: {bq_radius};" if bq_radius else ""
    parts.append(
        f"blockquote {{ border-left: {bq_cfg.get('border_left', '4px solid #4a90d9')}; "
        f"color: {bq_text}; background: {bq_bg}; "
        f"padding: {bq_cfg.get('padding', '8px 12px')}; margin: 0.85em 0; "
        f"{bq_break} {bq_extras} }} "
        f"blockquote p:last-child {{ margin-bottom: 0; }}"
    )

    # 分隔线
    parts.append("hr { border: none; border-top: 1px solid #d0d7de; margin: 1.5em 0; }")

    # 图片
    parts.append(
        "img, svg { max-width: 100%; height: auto; } img { display: block; margin: 0.85em auto; }"
    )

    # 数学公式
    parts.append("math { font-size: 1em; }")

    # 超链接
    parts.append(f"a {{ color: {link_color}; text-decoration: underline; }}")

    # pandoc 生成物的辅助样式
    parts.append(
        ".footnotes { font-size: 10pt; margin-top: 2em; border-top: 1px solid #d0d7de; } "
        ".footnotes ol { padding-left: 1.5em; } "
        "dl { margin: 0.85em 0; } dt { font-weight: 700; margin-top: 0.5em; } "
        "dd { margin-left: 1.5em; margin-bottom: 0.3em; }"
    )

    return "\n".join(parts)


def build_html_document(body: str, css: str) -> str:
    """将 CSS 和 HTML body 组装为完整 HTML 文档.

    Args:
        body: pandoc 输出的 HTML body 内容.
        css: 由 generate_css() 生成的 CSS 字符串.

    Returns:
        完整的 HTML 文档字符串.
    """
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; script-src 'none';">
  <style>
{css}
  </style>
</head>
<body>
{body}
</body>
</html>"""
