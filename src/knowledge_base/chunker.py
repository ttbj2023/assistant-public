"""Markdown 结构感知分块器.

按标题层级切分文档, 每个块注入"标题链"上下文(如 "中国茶经 / 第一章 茶树 / 一、品种"),
使块自包含 —— 脱离原文仍能被 embedding 准确表征, 这是召回精度的核心杠杆.

入库前预处理: <br> 转空格, 单行 HTML 表格转 GFM pipe 行(去标签噪声, 表格事实整行进入 embedding).
整行图片引用从正文剥离(embedding 噪声), 但原始路径按所在节记录进 chunk metadata 的
images 字段(逗号分隔), 供索引层解析为语料相对路径并暴露给读图工具.

超长节按段落边界二次切分并保留 overlap; 超长表格块按行边界切分且续块重复表头
(与标题链同哲学: 块自包含), 绝不在行中间截断; 单段仍超长则硬切兜底.
"""

from __future__ import annotations

import re

from langchain_core.documents import Document

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_PARAGRAPH_SEP_RE = re.compile(r"\n\s*\n")
_IMAGE_LINE_RE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]*)\)\s*$")
_BR_RE = re.compile(r"<br\s*/?>")
_TABLE_LINE_RE = re.compile(r"^\s*<table[^>]*>(.*)</table>\s*$")
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>")
_TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>")
_DEFAULT_MAX_CHUNK_CHARS = 800
_DEFAULT_OVERLAP_CHARS = 100
_CHAIN_SEP = " / "
_CONTEXT_PREFIX = "[章节] "


class MarkdownChunker:
    """按 Markdown 标题结构切分, 输出携带标题链元数据的 Document 块."""

    def __init__(
        self,
        max_chunk_chars: int = _DEFAULT_MAX_CHUNK_CHARS,
        overlap_chars: int = _DEFAULT_OVERLAP_CHARS,
    ) -> None:
        self.max_chunk_chars = max_chunk_chars
        self.overlap_chars = overlap_chars

    def chunk(self, text: str) -> list[Document]:
        """切分 Markdown 文本为带标题链的块列表.

        Args:
            text: Markdown 全文

        Returns:
            Document 列表; page_content = 标题链上下文行 + 节正文,
            metadata 含 heading_chain / section_title / chunk_index,
            节内含图片引用时另含 images(逗号分隔的原始引用路径)

        """
        if not text or not text.strip():
            return []

        text = self._preprocess(text)
        documents: list[Document] = []
        for chain, body, images in self._split_sections(text):
            for piece in self._split_body(body):
                metadata = {
                    "heading_chain": _CHAIN_SEP.join(chain),
                    "section_title": chain[-1] if chain else "",
                    "chunk_index": len(documents),
                }
                if images:
                    metadata["images"] = ",".join(images)
                documents.append(
                    Document(
                        page_content=self._compose(chain, piece),
                        metadata=metadata,
                    )
                )
        return documents

    def extract_image_anchors(self, text: str) -> list[tuple[list[str], str]]:
        """提取全文图片锚点: (标题链, 原始引用路径) 列表, 与正文块无关.

        供索引层解析为语料相对路径并生成图片块;
        仅含图片无正文的节也能由此获得锚点.
        """
        if not text or not text.strip():
            return []
        text = self._preprocess(text)
        anchors: list[tuple[list[str], str]] = []
        for chain, _body, images in self._split_sections(text):
            for raw_path in images:
                anchors.append((list(chain), raw_path))
        return anchors

    @staticmethod
    def _preprocess(text: str) -> str:
        """入库前清洗: <br> 转空格, 单行 HTML 表格转 GFM pipe 行."""
        text = _BR_RE.sub(" ", text)
        lines = []
        for line in text.split("\n"):
            lines.append(_convert_table_line(line))
        return "\n".join(lines)

    def _split_sections(self, text: str) -> list[tuple[list[str], str, list[str]]]:
        """按标题边界切分为 (标题链, 节正文, 节内图片引用路径) 序列.

        用标题栈维护层级: 遇到新标题时弹出 >= 当前层级的栈顶, 栈内标题即标题链.
        首个标题前的内容以空标题链独立成节(前言).
        整行图片引用从正文剥离(embedding 噪声), 原始路径按所在节记录进 images.
        """
        sections: list[tuple[list[str], str, list[str]]] = []
        stack: list[tuple[int, str]] = []
        current_chain: list[str] = []
        body_lines: list[str] = []
        section_images: list[str] = []

        def flush() -> None:
            body = "\n".join(body_lines).strip()
            if body or section_images:
                sections.append((list(current_chain), body, list(section_images)))

        for line in text.split("\n"):
            image_match = _IMAGE_LINE_RE.match(line)
            if image_match:
                section_images.append(image_match.group(2))
                continue
            match = _HEADING_RE.match(line)
            if match:
                flush()
                body_lines = []
                section_images = []
                level = len(match.group(1))
                title = match.group(2).strip()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                current_chain = [t for _, t in stack]
            else:
                body_lines.append(line)
        flush()
        return sections

    def _split_body(self, body: str) -> list[str]:
        """节正文切分: 不超长直接返回; 超长按段落 + overlap 切, 单段超长硬切兜底.

        超长表格块(pipe 行)独立按行边界切分, 续块重复表头 —— 绝不在行中间截断.
        """
        if len(body) <= self.max_chunk_chars:
            return [body]

        paragraphs = [p.strip() for p in _PARAGRAPH_SEP_RE.split(body) if p.strip()]
        pieces: list[str] = []
        buf = ""
        for para in paragraphs:
            if self._is_table_block(para) and len(para) > self.max_chunk_chars:
                if buf:
                    pieces.append(buf)
                    buf = ""
                pieces.extend(self._split_table(para))
                continue
            if buf and len(buf) + len(para) + 2 > self.max_chunk_chars:
                pieces.append(buf)
                tail = buf[-self.overlap_chars :] if self.overlap_chars > 0 else ""
                buf = f"{tail}\n\n{para}".strip() if tail else para
            else:
                buf = f"{buf}\n\n{para}".strip() if buf else para
        if buf:
            pieces.append(buf)

        result: list[str] = []
        for piece in pieces:
            result.extend(self._hard_split(piece))
        return result

    @staticmethod
    def _is_table_block(paragraph: str) -> bool:
        """判断段落是否为转换后的 pipe 表格块."""
        return paragraph.startswith("|")

    def _split_table(self, table: str) -> list[str]:
        """超长表格按行边界切分, 每个续块重复表头行 + 分隔行(与标题链同哲学: 块自包含)."""
        lines = [ln for ln in table.split("\n") if ln.strip()]
        if len(lines) <= 2:
            return [table]
        header = "\n".join(lines[:2])
        groups: list[str] = []
        buf = header
        for row in lines[2:]:
            if len(buf) + len(row) + 1 > self.max_chunk_chars and buf != header:
                groups.append(buf)
                buf = f"{header}\n{row}"
            else:
                buf = f"{buf}\n{row}"
        groups.append(buf)
        return groups

    def _hard_split(self, text: str) -> list[str]:
        """超长块兜底切分.

        多行块(段落+表格混合/含超长表格行)按行边界切: 行是表格事实的原子单位,
        绝不在行中间截断; 单行超长(如编年史单元格)允许整行成块, 略超预算可容忍.
        无换行的纯文本段落维持字符级滑窗 + overlap.
        """
        if len(text) <= self.max_chunk_chars:
            return [text]
        if "\n" not in text:
            step = max(1, self.max_chunk_chars - self.overlap_chars)
            return [
                text[i : i + self.max_chunk_chars] for i in range(0, len(text), step)
            ]
        pieces: list[str] = []
        buf = ""
        for ln in text.split("\n"):
            if buf and len(buf) + len(ln) + 1 > self.max_chunk_chars:
                pieces.append(buf)
                buf = ln
            else:
                buf = f"{buf}\n{ln}" if buf else ln
        if buf:
            pieces.append(buf)
        return pieces

    @staticmethod
    def _compose(chain: list[str], piece: str) -> str:
        """块内容 = 标题链上下文行 + 正文; 无标题链(前言)则仅正文."""
        if chain:
            return f"{_CONTEXT_PREFIX}{_CHAIN_SEP.join(chain)}\n\n{piece}"
        return piece


def _convert_table_line(line: str) -> str:
    """单行 HTML 表格转 GFM pipe 行(首行视作表头, 后补分隔行); 非表格行原样返回."""
    match = _TABLE_LINE_RE.match(line)
    if not match:
        return line
    rows = []
    for tr in _TR_RE.findall(match.group(1)):
        cells = [c.strip() for c in _TD_RE.findall(tr)]
        if cells:
            rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return line  # 解析不出任何行, 保守保留原文
    column_count = rows[0].count("|") - 1
    separator = "| " + " | ".join(["---"] * column_count) + " |"
    return "\n".join([rows[0], separator, *rows[1:]])


__all__ = ["MarkdownChunker"]
