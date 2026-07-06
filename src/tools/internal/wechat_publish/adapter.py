"""微信HTML/CSS规范适配器.

从 wechat_pusher 项目迁移, 对HTML进行6 pass过滤, 确保符合微信公众号渲染规范:
1. 标签白名单过滤
2. CSS属性白名单过滤 (移除 <style> 标签, 内联样式过滤)
3. 代码块空格修复 (\u00a0)
4. 图片处理 (强制 max-width: 100%)
5. 链接处理 (target=_blank, 移除事件属性)
6. 禁止属性移除 (id/class/onclick/onload/onerror/onmouseover)
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

_ALLOWED_TAGS: set[str] = {
    "p",
    "span",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "del",
    "s",
    "h1",
    "h2",
    "h3",
    "ul",
    "ol",
    "li",
    "blockquote",
    "code",
    "pre",
    "section",
    "div",
    "hr",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "img",
    "a",
}

_ALLOWED_CSS: set[str] = {
    "font-size",
    "font-weight",
    "font-style",
    "color",
    "text-align",
    "line-height",
    "letter-spacing",
    "text-decoration",
    "text-indent",
    "word-break",
    "white-space",
    "list-style-type",
    "width",
    "max-width",
    "min-width",
    "margin",
    "margin-top",
    "margin-right",
    "margin-bottom",
    "margin-left",
    "padding",
    "padding-top",
    "padding-right",
    "padding-bottom",
    "padding-left",
    "display",
    "flex-direction",
    "justify-content",
    "align-items",
    "flex-wrap",
    "flex",
    "gap",
    "box-sizing",
    "border",
    "border-top",
    "border-right",
    "border-bottom",
    "border-left",
    "border-radius",
    "background-color",
    "box-shadow",
    "opacity",
}

_FORBIDDEN_ATTRS: set[str] = {
    "id",
    "class",
    "onclick",
    "onload",
    "onerror",
    "onmouseover",
}

_BLOCK_ELEMENTS: set[str] = {"div", "section", "article", "header", "footer", "nav"}


def sanitize(html: str) -> str:
    """将HTML适配为微信兼容格式."""
    soup = BeautifulSoup(html, "lxml")
    _filter_tags(soup)
    _process_styles(soup)
    _fix_code_blocks(soup)
    _process_images(soup)
    _process_links(soup)
    _remove_forbidden_attributes(soup)
    return str(soup)


def _filter_tags(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(True):
        if tag.name not in _ALLOWED_TAGS:
            if tag.name in {"script", "style", "iframe", "form", "input"}:
                tag.decompose()
            else:
                _convert_tag(tag)


def _convert_tag(tag: Tag) -> None:
    if tag.name in _BLOCK_ELEMENTS:
        tag.name = "section"
    else:
        tag.name = "p"


def _process_styles(soup: BeautifulSoup) -> None:
    for style_tag in soup.find_all("style"):
        style_tag.decompose()

    for tag in soup.find_all(True):
        if tag.has_attr("style"):
            cleaned = _clean_style(tag["style"])
            if cleaned:
                tag["style"] = cleaned
            else:
                del tag["style"]


def _clean_style(style: str) -> str:
    if not style:
        return ""
    parts: list[str] = []
    for segment in style.split(";"):
        segment = segment.strip()
        if ":" not in segment:
            continue
        prop, value = segment.split(":", 1)
        prop = prop.strip().lower()
        if prop in _ALLOWED_CSS:
            parts.append(f"{prop}: {value.strip()}")
    return "; ".join(parts)


def _fix_code_blocks(soup: BeautifulSoup) -> None:
    for pre in soup.find_all("pre"):
        code = pre.find("code")
        if not code:
            continue
        lines = code.get_text().split("\n")
        processed: list[str] = []
        for line in lines:
            indent_match = re.match(r"^(\s+)", line)
            if indent_match:
                indent = indent_match.group(1)
                line = "\u00a0" * len(indent) + line[len(indent) :]
            processed.append(line)
        code.string = "\n".join(processed)


def _process_images(soup: BeautifulSoup) -> None:
    for img in soup.find_all("img"):
        style = img.get("style", "")
        if "max-width" not in style.lower():
            style = (
                f"{style}; max-width: 100%; height: auto;"
                if style
                else "max-width: 100%; height: auto;"
            )
            img["style"] = style
        for attr in list(img.attrs):
            if attr.startswith("on"):
                del img[attr]


def _process_links(soup: BeautifulSoup) -> None:
    for a in soup.find_all("a"):
        if a.has_attr("id"):
            del a["id"]
        for attr in list(a.attrs):
            if attr.startswith("on"):
                del a[attr]
        if not a.has_attr("target"):
            a["target"] = "_blank"


def _remove_forbidden_attributes(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(True):
        for attr in _FORBIDDEN_ATTRS:
            if tag.has_attr(attr):
                del tag[attr]


__all__ = ["sanitize"]
