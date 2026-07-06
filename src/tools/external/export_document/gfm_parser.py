"""GFM 源码解析器 - 提取目录结构.

从 GFM (GitHub Flavored Markdown) 源码中自动提取:
- TOC: 标题层级 + 行号 + 标题文本
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


@dataclass
class TocEntry:
    """目录条目."""

    level: int
    title: str
    line: int


@dataclass
class DocumentStructure:
    """文档结构化元数据."""

    summary: str = ""
    toc: list[TocEntry] = field(default_factory=list)
    format: str = ""

    def to_json_dict(self) -> dict:
        return {
            "summary": self.summary,
            "toc": [asdict(e) for e in self.toc],
            "format": self.format,
        }


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


def parse_gfm_structure(content: str, output_format: str) -> DocumentStructure:
    """解析 GFM 源码, 提取目录信息.

    Args:
        content: GFM 源码
        output_format: 输出格式 (pdf/docx)

    Returns:
        DocumentStructure 包含 toc
    """
    lines = content.split("\n")
    toc: list[TocEntry] = []

    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            toc.append(TocEntry(level=level, title=title, line=i))

    return DocumentStructure(
        toc=toc,
        format=output_format,
    )
