"""书目元数据(book.yaml)模型与加载 - 目录即书.

领域语料根下每个含 Markdown 的一级子目录即一部资料(书/论文/评测),
book.yaml 是这部资料的书目卡: title/type/author/source, 为每个块注入出处元数据,
支撑检索结果的出处引用与按类型过滤.

宽容默认: book.yaml 缺失或缺字段时, title 回填目录名, type 默认 book, author/source 留空.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_VALID_DOC_TYPES = frozenset({"book", "paper", "review", "article"})
_BOOK_META_FILENAME = "book.yaml"


class BookMetadata(BaseModel):
    """一部资料的书目元数据."""

    title: str = Field(default="", description="资料标题(用于出处引用), 空=回填目录名")
    type: str = Field(default="book", description="资料类型: book/paper/review/article")
    author: str = Field(default="", description="作者")
    source: str = Field(default="", description="来源(出版社/期刊/网站等)")


def load_book_metadata(book_dir: str | Path) -> BookMetadata:
    """从资料目录加载 book.yaml, 缺失时按目录名给宽容默认.

    Args:
        book_dir: 资料目录(如 data/tea/中国茶经)

    Returns:
        校验并回填后的 BookMetadata

    Raises:
        ValueError: type 字段非法

    """
    directory = Path(book_dir)
    path = directory / _BOOK_META_FILENAME
    raw: dict = {}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    metadata = BookMetadata.model_validate(raw)
    if metadata.type not in _VALID_DOC_TYPES:
        raise ValueError(
            f"非法文档类型 '{metadata.type}' (book_dir={directory}), "
            f"允许: {sorted(_VALID_DOC_TYPES)}"
        )
    if not metadata.title:
        metadata = metadata.model_copy(update={"title": directory.name})
    return metadata


__all__ = ["BookMetadata", "load_book_metadata"]
