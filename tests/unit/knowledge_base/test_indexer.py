"""KnowledgeBaseIndexer 增量索引测试 - 目录即书, 自动发现.

真实 Chroma + mock embeddings; 语料用临时目录构造(一部带 book.yaml 的书,
一部无 book.yaml 的资料, 一个无 Markdown 的空目录).
"""

from __future__ import annotations

import pytest

from src.knowledge_base.chunker import MarkdownChunker
from src.knowledge_base.indexer import KnowledgeBaseIndexer
from src.knowledge_base.store import KnowledgeBaseStore
from tests.mocks.unified_factory import UnifiedMockFactory


@pytest.fixture
def store(tmp_path):
    return KnowledgeBaseStore(
        "tea",
        persist_directory=str(tmp_path / "kb"),
        embeddings=UnifiedMockFactory.create_embeddings(realistic=True),
    )


@pytest.fixture
def corpus(tmp_path):
    """领域语料根: 两部资料 + 一个无 Markdown 的空目录(不应被发现)."""
    root = tmp_path / "tea"
    book = root / "中国茶经"
    book.mkdir(parents=True)
    (book / "book.yaml").write_text(
        "title: 中国茶经\ntype: book\nauthor: 陈宗懋\nsource: 上海文化出版社\n",
        encoding="utf-8",
    )
    (book / "茶史篇.md").write_text(
        "# 中国茶经\n\n## 茶史\n\n茶树起源内容。", encoding="utf-8"
    )
    review = root / "审评"
    review.mkdir()
    (review / "审评.md").write_text("# 审评\n\n龙井审评内容。", encoding="utf-8")
    (root / "空目录").mkdir()
    return root


@pytest.fixture
def indexer(store):
    return KnowledgeBaseIndexer(store, MarkdownChunker())


class _FakeDescriber:
    """内联 Mock: 固定题注, 记录调用."""

    def __init__(self, caption: str | None = "杀青机结构示意图, 画面为机械剖面图"):
        self.caption = caption
        self.calls: list[str] = []

    async def describe(self, corpus_root, rel_path):
        self.calls.append(rel_path)
        return self.caption


@pytest.fixture
def corpus_with_images(tmp_path):
    """含图片引用与图片文件的语料."""
    root = tmp_path / "tea"
    book = root / "中国茶经"
    (book / "parts").mkdir(parents=True)
    (book / "book.yaml").write_text(
        "title: 中国茶经\ntype: book\n", encoding="utf-8"
    )
    (book / "parts" / "茶技篇.md").write_text(
        "# 中国茶经\n\n## 杀青\n\n滚筒杀青说明。\n\n![图版](../images/page0415_028.jpg)",
        encoding="utf-8",
    )
    img_dir = book / "images"
    img_dir.mkdir()
    (img_dir / "page0415_028.jpg").write_bytes(b"fake-image")
    return root


class TestImageChunks:
    async def test_build_with_describer_creates_image_chunk(
        self, store, corpus_with_images
    ):
        describer = _FakeDescriber()
        indexer = KnowledgeBaseIndexer(
            store, MarkdownChunker(), image_describer=describer
        )
        stats = await indexer.build(corpus_with_images)

        results = await store.search("滚筒杀青说明", top_k=10)
        image_chunks = [r for r in results if r.metadata.get("chunk_type") == "image"]
        assert len(image_chunks) == 1
        meta = image_chunks[0].metadata
        # 原始相对引用已解析为语料根相对路径
        assert meta["image_path"] == "中国茶经/images/page0415_028.jpg"
        assert meta["doc_ref"] == "中国茶经/parts/茶技篇.md"
        assert meta["doc_title"] == "中国茶经"
        assert meta["heading_chain"] == "中国茶经 / 杀青"
        assert "杀青机结构示意图" in image_chunks[0].page_content
        # 描述器按解析后的路径调用
        assert describer.calls == ["中国茶经/images/page0415_028.jpg"]
        assert stats.images == 1

    async def test_build_without_describer_skips_image_chunks(
        self, store, corpus_with_images
    ):
        indexer = KnowledgeBaseIndexer(store, MarkdownChunker())
        await indexer.build(corpus_with_images)
        results = await store.search("滚筒杀青说明", top_k=10)
        assert not [r for r in results if r.metadata.get("chunk_type") == "image"]

    async def test_describer_failure_skips_image_chunk(
        self, store, corpus_with_images
    ):
        class _Failing:
            async def describe(self, corpus_root, rel_path):
                return None

        indexer = KnowledgeBaseIndexer(
            store, MarkdownChunker(), image_describer=_Failing()
        )
        stats = await indexer.build(corpus_with_images)
        assert stats.images == 0
        results = await store.search("滚筒杀青说明", top_k=10)
        assert not [r for r in results if r.metadata.get("chunk_type") == "image"]

    async def test_text_chunk_images_metadata_resolved_to_corpus_relative(
        self, store, corpus_with_images
    ):
        indexer = KnowledgeBaseIndexer(
            store, MarkdownChunker(), image_describer=_FakeDescriber()
        )
        await indexer.build(corpus_with_images)
        results = await store.search("滚筒杀青说明", top_k=10)
        text_chunks = [r for r in results if r.metadata.get("chunk_type") != "image"]
        section = next(c for c in text_chunks if "滚筒杀青" in c.page_content)
        assert section.metadata["images"] == "中国茶经/images/page0415_028.jpg"


class TestBuildDiscovery:
    async def test_build_discovers_book_dirs_only(self, indexer, store, corpus):
        stats = await indexer.build(corpus)
        # 空目录无 Markdown, 不计入
        assert stats.total == 2
        assert stats.indexed == 2
        assert store.count() > 0

    async def test_chunks_carry_book_yaml_metadata(self, indexer, store, corpus):
        await indexer.build(corpus)
        results = await store.search("茶树起源", top_k=5)
        book_chunks = [r for r in results if r.metadata.get("doc_title") == "中国茶经"]
        assert book_chunks
        assert book_chunks[0].metadata["doc_type"] == "book"
        assert book_chunks[0].metadata["author"] == "陈宗懋"
        assert book_chunks[0].metadata["source"] == "上海文化出版社"
        # doc_ref 含书目录, 跨书唯一
        assert book_chunks[0].metadata["doc_ref"] == "中国茶经/茶史篇.md"
        assert book_chunks[0].metadata["kb_name"] == "tea"

    async def test_missing_book_yaml_falls_back_to_dir_name(
        self, indexer, store, corpus
    ):
        await indexer.build(corpus)
        results = await store.search("龙井审评", top_k=5)
        review_chunks = [
            r for r in results if r.metadata.get("doc_ref") == "审评/审评.md"
        ]
        assert review_chunks
        assert review_chunks[0].metadata["doc_title"] == "审评"
        assert review_chunks[0].metadata["doc_type"] == "book"


class TestIncremental:
    async def test_second_run_skips_unchanged(self, indexer, corpus):
        await indexer.build(corpus)
        stats = await indexer.build(corpus)
        assert stats.skipped == 2
        assert stats.indexed == 0

    async def test_build_writes_kb_meta_with_corpus_root(self, store, corpus):
        import json as json_mod
        from pathlib import Path as PathMod

        await KnowledgeBaseIndexer(store, MarkdownChunker()).build(corpus)
        meta_path = PathMod(store.persist_directory) / "kb_meta.json"
        assert meta_path.exists()
        data = json_mod.loads(meta_path.read_text(encoding="utf-8"))
        assert data["corpus_root"] == str(corpus.resolve())

    async def test_changed_file_is_updated(self, indexer, store, corpus):
        await indexer.build(corpus)
        (corpus / "中国茶经" / "茶史篇.md").write_text(
            "# 中国茶经\n\n## 茶史\n\n全新改写的内容, 完全不同。", encoding="utf-8"
        )
        stats = await indexer.build(corpus)
        assert stats.updated == 1
        assert stats.skipped == 1
        assert store.count() >= 1

    async def test_rebuild_reindexes_everything(self, indexer, corpus):
        await indexer.build(corpus)
        stats = await indexer.build(corpus, rebuild=True)
        assert stats.indexed == 2
        assert stats.skipped == 0
