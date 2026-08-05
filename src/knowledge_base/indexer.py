"""知识库增量索引器 - 目录即书: 领域语料根 → 结构感知分块 → 向量库.

组织约定: 语料根下每个含 >=1 个 Markdown 的一级子目录即一部资料(书/论文/评测),
book.yaml 提供书目元数据(缺省宽容默认); doc_ref = 相对语料根路径(含资料目录), 跨书唯一.

增量幂等: 以 文档内容 sha256 判重, 未变更跳过; 变更/重建时先按 doc_ref 删旧块再重写.
索引状态(各文档 content hash)以 JSON 落向量库目录的 index_state.json.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.knowledge_base.book import BookMetadata, load_book_metadata
from src.knowledge_base.chunker import MarkdownChunker
from src.knowledge_base.store import KnowledgeBaseStore

logger = logging.getLogger(__name__)

_STATE_FILENAME = "index_state.json"


@dataclass
class IndexStats:
    """一次索引的统计."""

    total: int = 0
    indexed: int = 0
    skipped: int = 0
    updated: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class KnowledgeBaseIndexer:
    """扫描领域语料根, 增量将自动发现的 Markdown 文档索引进向量库."""

    def __init__(
        self,
        store: KnowledgeBaseStore,
        chunker: MarkdownChunker | None = None,
    ) -> None:
        self.store = store
        self.chunker = chunker or MarkdownChunker()

    async def build(
        self,
        corpus_root: str | Path,
        *,
        rebuild: bool = False,
    ) -> IndexStats:
        """索引语料根下自动发现的全部文档.

        Args:
            corpus_root: 领域语料根(一级子目录 = 一部资料)
            rebuild: True 时忽略状态全量重建

        Returns:
            索引统计

        """
        root = Path(corpus_root)
        state = {} if rebuild else self._load_state()
        documents = self._discover(root)
        stats = IndexStats(total=len(documents))

        for doc_ref, path, book in documents:
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as e:
                stats.failed += 1
                stats.errors.append(f"{doc_ref}: {e}")
                logger.warning("读取文档失败 %s: %s", doc_ref, e)
                continue

            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if state.get(doc_ref) == content_hash:
                stats.skipped += 1
                continue

            is_update = doc_ref in state
            if is_update or rebuild:
                await self.store.delete_by_doc_ref(doc_ref)

            chunks = self.chunker.chunk(content)
            doc_meta = {
                "doc_ref": doc_ref,
                "doc_title": book.title,
                "doc_type": book.type,
                "author": book.author,
                "source": book.source,
                "kb_name": self.store.kb_name,
            }
            for chunk in chunks:
                chunk.metadata.update(doc_meta)
            ids = [f"{doc_ref}::{i}" for i in range(len(chunks))]
            await self.store.add_documents(chunks, ids)

            state[doc_ref] = content_hash
            if is_update:
                stats.updated += 1
            else:
                stats.indexed += 1
            logger.info("已索引 %s: %d 块", doc_ref, len(chunks))

        self._save_state(state)
        return stats

    @staticmethod
    def _discover(root: Path) -> list[tuple[str, Path, BookMetadata]]:
        """发现语料根下全部资料: 含 >=1 个 .md 的一级子目录(跳过隐藏目录)."""
        documents: list[tuple[str, Path, BookMetadata]] = []
        for book_dir in sorted(
            p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
        ):
            md_files = sorted(book_dir.rglob("*.md"))
            if not md_files:
                continue
            book = load_book_metadata(book_dir)
            for md in md_files:
                documents.append((md.relative_to(root).as_posix(), md, book))
        return documents

    def _state_path(self) -> Path:
        return Path(self.store.persist_directory) / _STATE_FILENAME

    def _load_state(self) -> dict[str, str]:
        path = self._state_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("索引状态损坏, 视为空: %s", e)
            return {}

    def _save_state(self, state: dict[str, str]) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )


__all__ = ["IndexStats", "KnowledgeBaseIndexer"]
