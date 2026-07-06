"""Markdown → 微信公众号HTML转换器.

从 wechat_pusher 项目迁移核心转换逻辑, 接口简化为纯函数.
使用 python-markdown 做基础转换, 然后大量后处理添加微信内联样式.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from html import unescape

import markdown
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

_FONT_FAMILY = (
    '-apple-system, BlinkMacSystemFont, "PingFang SC", '
    '"Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", '
    '"Helvetica Neue", Arial, sans-serif'
)


def md_to_wechat_html(
    content: str, summary: str | None = None, author: str = ""
) -> str:
    """将 Markdown 转为微信兼容 HTML.

    Args:
        content: Markdown 正文 (不含标题, 标题由调用方单独处理)
        summary: 可选摘要, 显示在正文开头的引用框中
        author: 可选作者, 显示在文末分隔线后

    """
    md = markdown.Markdown(extensions=["extra", "sane_lists", "fenced_code"])
    html = md.convert(content)

    soup = BeautifulSoup(html, "lxml")

    _remove_first_h1(soup)
    _style_headings(soup)
    _style_paragraphs(soup)
    _style_lists(soup)
    _style_bold(soup)
    _style_strikethrough(soup)
    _style_inline_code(soup)
    _style_code_blocks(soup)
    _process_latex(soup)
    _cleanup_tags(soup)

    content_html = str(soup).strip()

    summary_section = ""
    if summary:
        summary_section = (
            '<section style="width: 100%; padding: 0px 16px;">'
            '<section style="width: 100%; text-align: center; margin: 24px 0;">'
            '<section style="display: inline-block; max-width: 100%; text-align: left; '
            "background-color: rgba(96, 125, 139, 0.15); padding: 20px 24px; "
            'border-radius: 12px; border-left: 4px solid #607D8B;">'
            f'<p style="font-size: 15px; color: #455A64; line-height: 1.8; '
            f"margin: 0; font-weight: 500; letter-spacing: 0.5px; "
            f'font-family: {_FONT_FAMILY};">{summary}</p>'
            "</section></section></section>"
        )

    now = datetime.now()
    date_str = f"{now.year} 年 {now.month} 月"

    author_line = ""
    if author:
        author_line = (
            '<p style="font-size: 14px; color: #666666; line-height: 1.6; '
            f'text-align: right; margin: 16px 0 4px 0;">文/ {author}</p>'
        )

    return (
        f'<section style="padding: 0px 8px; font-family: {_FONT_FAMILY};">\n'
        f"  {summary_section}\n"
        f'  <div style="font-size: 15px; margin-bottom: 16px; color: rgb(51, 51, 51); '
        f'margin-top: 16px; letter-spacing: 1px; font-family: {_FONT_FAMILY};">\n'
        f"    {content_html}\n"
        "  </div>"
        '<section style="margin-top: 32px;">'
        '<hr style="height: 1px; background-color: #e5e7eb; border: none;" />'
        f"{author_line}"
        f'<p style="font-size: 14px; color: #999999; line-height: 1.6; '
        f'text-align: right; margin: 4px 0 16px 0;">{date_str}</p>'
        '<p style="font-size: 14px; color: #666666; line-height: 1.6; '
        'text-align: center; margin: 16px 0;">感谢阅读</p>'
        "</section></section>"
    )


def _remove_first_h1(soup: BeautifulSoup) -> None:
    first_h1 = soup.find("h1")
    if first_h1:
        first_h1.decompose()


def _style_headings(soup: BeautifulSoup) -> None:
    for heading in soup.find_all(["h2", "h3"]):
        text = heading.get_text().strip()
        heading.clear()
        if heading.name == "h2":
            heading["style"] = (
                "display: inline-block; margin: 0; "
                "padding: 6px 16px; text-align: center; line-height: 1.6; "
                "font-size: 17px; font-weight: bold; color: rgb(255, 255, 255); "
                "background-color: rgb(15, 76, 129); border-radius: 4px; max-width: 100%;"
            )
            wrapper = soup.new_tag("section")
            wrapper["style"] = "text-align: center; margin: 32px auto 16px;"
            heading.wrap(wrapper)
        else:
            heading["style"] = (
                "display: inline-block; margin: 24px auto 12px; "
                "padding: 6px 12px; line-height: 1.6; font-size: 16px; "
                "font-weight: bold; color: rgb(15, 76, 129); "
                "border-left: 4px solid rgb(15, 76, 129); "
                "background-color: rgb(240, 244, 248); border-radius: 0 4px 4px 0; "
                "max-width: 100%;"
            )
        heading.string = text


def _style_paragraphs(soup: BeautifulSoup) -> None:
    for p in soup.find_all("p"):
        if not p.get("style"):
            p["style"] = (
                f"font-size: 15px; margin-bottom: 16px; margin-top: 16px; "
                f"line-height: 1.75; color: rgb(51, 51, 51); "
                f"letter-spacing: 1px; font-family: {_FONT_FAMILY};"
            )


def _style_lists(soup: BeautifulSoup) -> None:
    """微信不支持 list-style-type 渲染, 改用 <section>/<p> + 手动符号."""
    for ul in soup.find_all("ul"):
        for li in ul.find_all("li", recursive=False):
            li_inner = _style_list_item_inner(li)
            li.clear()
            li.name = "p"
            li["style"] = (
                "font-size: 15px; line-height: 1.75; margin: 8px 0; "
                "color: rgb(51, 51, 51); letter-spacing: 1px; "
                "padding-left: 16px;"
            )
            li.append(BeautifulSoup(f"• {li_inner}", "lxml").find("body").next_element)

        _unwrap_to_section(ul, "margin: 16px 0; padding-left: 8px;")

    for ol in soup.find_all("ol"):
        for idx, li in enumerate(ol.find_all("li", recursive=False), start=1):
            li_inner = _style_list_item_inner(li)
            li.clear()
            li.name = "p"
            li["style"] = (
                "font-size: 15px; line-height: 1.75; margin: 8px 0; "
                "color: rgb(51, 51, 51); letter-spacing: 1px; "
                "padding-left: 16px;"
            )
            li.append(
                BeautifulSoup(f"{idx}. {li_inner}", "lxml").find("body").next_element
            )

        _unwrap_to_section(ol, "margin: 16px 0; padding-left: 8px;")


def _style_list_item_inner(li_tag: Tag) -> str:
    """提取 li 内部 HTML (保留加粗/代码等内联格式), 处理嵌套列表."""
    inner_parts: list[str] = []

    for child in li_tag.children:
        if isinstance(child, Tag) and child.name in {"ul", "ol"}:
            continue
        inner_parts.append(str(child))

    return "".join(inner_parts).strip()


def _unwrap_to_section(list_tag: Tag, style: str) -> None:
    """将 ul/ol 替换为 section 包裹的 p 列表."""
    section = soup_new_tag("section")
    section["style"] = style

    for child in list(list_tag.children):
        if isinstance(child, Tag):
            section.append(child.extract())

    list_tag.replace_with(section)


def soup_new_tag(name: str) -> Tag:
    """创建独立的 BeautifulSoup Tag."""
    tag = BeautifulSoup(f"<{name}></{name}>", "lxml").find(name)
    assert tag is not None
    return tag


def _style_bold(soup: BeautifulSoup) -> None:
    for bold in soup.find_all(["strong", "b"]):
        if not bold.get("style"):
            bold["style"] = "color: rgb(15, 76, 129); font-weight: bold;"


def _style_strikethrough(soup: BeautifulSoup) -> None:
    def _replace_strikethrough(text: str) -> str:
        return re.sub(
            r"~~([^\~]+?)~~",
            lambda m: (
                f'<del style="color: #999999; text-decoration: line-through;">{m.group(1)}</del>'
            ),
            text,
            flags=re.DOTALL,
        )

    for element in soup.find_all(string=True):
        if "~~" in str(element):
            new_text = _replace_strikethrough(str(element))
            if new_text != str(element):
                element.replace_with(BeautifulSoup(new_text, "lxml"))

    for del_tag in soup.find_all(["del", "s", "strike"]):
        if not del_tag.get("style"):
            del_tag["style"] = "color: #999999; text-decoration: line-through;"


def _style_inline_code(soup: BeautifulSoup) -> None:
    for code in soup.find_all("code"):
        if code.find_parent("pre") is None:
            code_text = code.get_text()
            code_html = (
                f'<span style="background-color: #f6f8fa; color: #d63384; '
                f"padding: 2px 6px; border-radius: 4px; "
                f"font-family: 'Courier New', Consolas, Monaco, monospace; "
                f'font-size: 0.9em;">{code_text}</span>'
            )
            code.replace_with(BeautifulSoup(code_html, "lxml"))


def _style_code_blocks(soup: BeautifulSoup) -> None:
    for pre in soup.find_all("pre"):
        code_tag = pre.find("code")
        if not code_tag:
            continue

        lines = code_tag.get_text().split("\n")
        processed: list[str] = []
        for line in lines:
            line = line.rstrip()
            leading = len(line) - len(line.lstrip())
            processed.append("&nbsp;" * leading + line.lstrip())

        code_html_content = "<br/>".join(processed)
        code_html = (
            '<section style="margin: 16px 0; padding: 0;">'
            '<section style="background-color: #f6f8fa; padding: 16px; '
            'border-radius: 8px; border-left: 4px solid #004080; overflow-x: auto;">'
            "<code style=\"font-family: 'Courier New', Consolas, Monaco, monospace; "
            "font-size: 13px; line-height: 1.8; color: #333; "
            f'white-space: nowrap; display: block;">{code_html_content}</code>'
            "</section></section>"
        )
        pre.replace_with(BeautifulSoup(code_html, "lxml"))


def _process_latex(soup: BeautifulSoup) -> None:
    for p in soup.find_all("p"):
        p_html = str(p)
        if "$" not in p_html:
            continue

        def _replace_block(m: re.Match[str]) -> str:
            formula = unescape(m.group(1).strip())
            unicode_text = _simple_latex_to_text(formula)
            return (
                '<section style="background-color: #f0f4f8; padding: 20px; '
                "border-radius: 8px; text-align: center; margin: 16px 0; "
                'border: 1px solid #cbd5e0;">'
                '<span style="background-color: #e7f3ff; color: #004080; '
                "padding: 8px 16px; border-radius: 6px; "
                "font-family: 'Times New Roman', serif; "
                f"font-size: 16px; letter-spacing: 0.5px; "
                f'display: inline-block;">{unicode_text}</span></section>'
            )

        def _replace_inline(m: re.Match[str]) -> str:
            formula = unescape(m.group(1).strip())
            unicode_text = _simple_latex_to_text(formula)
            return (
                '<span style="background-color: #e7f3ff; color: #004080; '
                "padding: 2px 8px; border-radius: 4px; "
                "font-family: 'Times New Roman', serif; "
                f'font-size: 0.95em; font-style: italic;">{unicode_text}</span>'
            )

        p_html = re.sub(r"\$\$([^$]+)\$\$", _replace_block, p_html)
        p_html = re.sub(r"\$([^$]+)\$", _replace_inline, p_html)
        p.replace_with(BeautifulSoup(p_html, "lxml"))


def _simple_latex_to_text(latex: str) -> str:
    """简单 LaTeX → Unicode 替换 (不依赖 pylatexenc)."""
    replacements = {
        r"\alpha": "α",
        r"\beta": "β",
        r"\gamma": "γ",
        r"\delta": "δ",
        r"\epsilon": "ε",
        r"\theta": "θ",
        r"\lambda": "λ",
        r"\mu": "μ",
        r"\sigma": "σ",
        r"\omega": "ω",
        r"\pi": "π",
        r"\phi": "φ",
        r"\sum": "∑",
        r"\prod": "∏",
        r"\int": "∫",
        r"\infty": "∞",
        r"\leq": "≤",
        r"\geq": "≥",
        r"\neq": "≠",
        r"\approx": "≈",
        r"\times": "×",
        r"\div": "÷",
        r"\pm": "±",
        r"\cdot": "·",
        r"\rightarrow": "→",
        r"\leftarrow": "←",
        r"\Rightarrow": "⇒",
        r"\in": "∈",
        r"\notin": "∉",
        r"\subset": "⊂",
        r"\supset": "⊃",
        r"\cup": "∪",
        r"\cap": "∩",
        r"\emptyset": "∅",
        r"\sqrt": "√",
        r"\partial": "∂",
        r"\nabla": "∇",
    }
    result = latex
    for latex_sym, unicode_sym in replacements.items():
        result = result.replace(latex_sym, unicode_sym)
    result = re.sub(r"[{}\\]", "", result)
    return result.strip()


def _cleanup_tags(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(["html", "body"]):
        tag.unwrap()
    for section in soup.find_all("section"):
        if not section.get_text().strip() and len(section.find_all()) == 0:
            section.decompose()


__all__ = ["md_to_wechat_html"]
