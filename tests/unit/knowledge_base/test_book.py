"""book.yaml 书目元数据模型与加载测试 - 目录即书."""

from __future__ import annotations

import pytest

from src.knowledge_base.book import BookMetadata, load_book_metadata


class TestLoadBookMetadata:
    def test_book_yaml_full_fields_loaded(self, tmp_path):
        book_dir = tmp_path / "中国茶经"
        book_dir.mkdir()
        (book_dir / "book.yaml").write_text(
            """
title: 中国茶经
type: book
author: 陈宗懋(主编)
source: 上海文化出版社
""",
            encoding="utf-8",
        )
        metadata = load_book_metadata(book_dir)
        assert metadata.title == "中国茶经"
        assert metadata.type == "book"
        assert metadata.author == "陈宗懋(主编)"
        assert metadata.source == "上海文化出版社"

    def test_missing_book_yaml_falls_back_to_dir_name(self, tmp_path):
        book_dir = tmp_path / "茶叶化学"
        book_dir.mkdir()
        metadata = load_book_metadata(book_dir)
        assert metadata.title == "茶叶化学"
        assert metadata.type == "book"
        assert metadata.author == ""
        assert metadata.source == ""

    def test_book_yaml_without_title_backfills_dir_name(self, tmp_path):
        book_dir = tmp_path / "某论文集"
        book_dir.mkdir()
        (book_dir / "book.yaml").write_text("type: paper\n", encoding="utf-8")
        metadata = load_book_metadata(book_dir)
        assert metadata.title == "某论文集"
        assert metadata.type == "paper"

    def test_invalid_type_raises(self, tmp_path):
        book_dir = tmp_path / "坏书"
        book_dir.mkdir()
        (book_dir / "book.yaml").write_text(
            "title: 坏书\ntype: 非法类型\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="非法文档类型"):
            load_book_metadata(book_dir)

    def test_returns_book_metadata_instance(self, tmp_path):
        book_dir = tmp_path / "书"
        book_dir.mkdir()
        assert isinstance(load_book_metadata(book_dir), BookMetadata)
