"""知识库增量索引器 - 目录即书: 领域语料根 → 结构感知分块 → 向量库.

组织约定: 语料根下每个含 >=1 个 Markdown 的一级子目录即一部资料(书/论文/评测),
book.yaml 提供书目元数据(缺省宽容默认); doc_ref = 相对语料根路径(含资料目录), 跨书唯一.

增量幂等: 以 文档内容 sha256 判重, 未变更跳过; 变更/重建时先按 doc_ref 删旧块再重写.
索引状态(各文档 content hash)以 JSON 落向量库目录的 index_state.json.

图片索引: 注入 image_describer 时, 文中整行图片引用解析为语料相对路径,
经视觉模型生成题注后作为独立图片块(chunk_type=image)入库;
正文块 metadata 的 images 字段同步改写为解析后的路径.
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.documents import Document

from src.knowledge_base.book import BookMetadata, load_book_metadata
from src.knowledge_base.chunker import MarkdownChunker
from src.knowledge_base.store import KnowledgeBaseStore

logger = logging.getLogger(__name__)

_STATE_FILENAME = "index_state.json"
_CHAIN_JOIN = " / "
_KB_META_FILENAME = "kb_meta.json"


@dataclass
class IndexStats:
    """一次索引的统计."""

    total: int = 0
    indexed: int = 0
    skipped: int = 0
    updated: int = 0
    failed: int = 0
    images: int = 0
    errors: list[str] = field(default_factory=list)


class KnowledgeBaseIndexer:
    """扫描领域语料根, 增量将自动发现的 Markdown 文档索引进向量库."""

    def __init__(
        self,
        store: KnowledgeBaseStore,
        chunker: MarkdownChunker | None = None,
        image_describer: object | None = None,
    ) -> None:
        self.store = store
        self.chunker = chunker or MarkdownChunker()
        self._image_describer = image_describer

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
            self._resolve_chunk_images(chunks, doc_ref)
            image_chunks = await self._build_image_chunks(content, root, doc_ref)
            stats.images += len(image_chunks)
            all_chunks = chunks + image_chunks
            for chunk in all_chunks:
                chunk.metadata.update(doc_meta)
            ids = [f"{doc_ref}::{i}" for i in range(len(all_chunks))]
            await self.store.add_documents(all_chunks, ids)

            state[doc_ref] = content_hash
            if is_update:
                stats.updated += 1
            else:
                stats.indexed += 1
            logger.info(
                "已索引 %s: %d 块 (含 %d 图片块)",
                doc_ref,
                len(all_chunks),
                len(image_chunks),
            )

        self._save_state(state)
        self._save_kb_meta(root)
        return stats

    def _save_kb_meta(self, corpus_root: Path) -> None:
        """语料根落 kb_meta.json, 供运行时读图工具解析 image_ref 寻址."""
        meta_path = Path(self.store.persist_directory) / _KB_META_FILENAME
        try:
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(
                json.dumps({"corpus_root": str(corpus_root.resolve())}),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("kb_meta.json 写入失败: %s, %s", meta_path, e)

    @staticmethod
    def _resolve_image_ref(doc_ref: str, raw_ref: str) -> str | None:
        """图片原始引用(md 相对路径) → 语料根相对路径; 逃逸语料根或空引用返回 None."""
        if not raw_ref:
            return None
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(posixpath.normpath(doc_ref)), raw_ref)
        )
        if not resolved or resolved.startswith("../") or posixpath.isabs(resolved):
            logger.warning("图片引用逃逸语料根, 忽略: doc=%s ref=%s", doc_ref, raw_ref)
            return None
        return resolved

    def _resolve_chunk_images(
        self,
        chunks: list[Document],
        doc_ref: str,
    ) -> None:
        """把正文块 metadata.images 的原始引用改写为语料根相对路径."""
        for chunk in chunks:
            raw = chunk.metadata.get("images")
            if not raw:
                continue
            resolved = [
                r
                for r in (self._resolve_image_ref(doc_ref, x) for x in raw.split(","))
                if r
            ]
            if resolved:
                chunk.metadata["images"] = ",".join(resolved)
            else:
                chunk.metadata.pop("images")

    async def _build_image_chunks(
        self,
        content: str,
        corpus_root: Path,
        doc_ref: str,
    ) -> list[Document]:
        """为文档中的图片锚点生成独立图片块(chunk_type=image).

        未注入 image_describer 或描述失败时跳过; 按解析后路径去重.
        """
        if self._image_describer is None:
            return []

        seen: set[str] = set()
        image_chunks: list[Document] = []
        for chain, raw_ref in self.chunker.extract_image_anchors(content):
            resolved = self._resolve_image_ref(doc_ref, raw_ref)
            if not resolved or resolved in seen:
                continue
            seen.add(resolved)

            caption = await self._image_describer.describe(corpus_root, resolved)
            if not caption:
                continue

            page_content = "\n".join([
                "[章节] " + _CHAIN_JOIN.join(chain),
                f"[图片] {caption}",
            ])
            image_chunks.append(
                Document(
                    page_content=page_content,
                    metadata={
                        "chunk_type": "image",
                        "image_path": resolved,
                        "heading_chain": _CHAIN_JOIN.join(chain),
                        "section_title": chain[-1] if chain else "",
                    },
                )
            )
        return image_chunks

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
