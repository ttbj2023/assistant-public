"""desc_writer 单元测试.

测试 .desc.md 描述文件的约定路径推导、写入、读取、删除, 以及最佳努力的异常隔离.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.files.desc_writer import (
    compose_desc,
    delete_desc,
    desc_abs_path,
    desc_relative_path,
    read_desc,
    split_desc,
    write_desc,
)


@pytest.fixture
def mock_user_base(tmp_path: Path):
    """mock user_base_path 指向临时目录."""
    resolver = MagicMock()
    resolver.get_user_base_path.return_value = tmp_path
    with patch("src.files.desc_writer.get_user_path_resolver", return_value=resolver):
        yield tmp_path


class TestComposeDesc:
    """测试统一描述结构组装: 摘要 + 分隔符 + 原文."""

    def test_composes_summary_and_original(self):
        result = compose_desc("一句话摘要", "line1\nline2")
        assert result == "一句话摘要\n\n---\n\nline1\nline2"

    def test_empty_summary_returns_original_only(self):
        assert compose_desc("", "原文内容") == "原文内容"


class TestSplitDesc:
    """测试统一描述结构解析 (与 compose_desc 互逆)."""

    def test_roundtrip(self):
        content = compose_desc("摘要", "line1\nline2")
        assert split_desc(content) == ("摘要", "line1\nline2")

    def test_legacy_content_without_separator(self):
        """旧格式 (统一前写入) 无分隔符: summary 空, 全文为原文."""
        assert split_desc("旧格式描述全文") == ("", "旧格式描述全文")

    def test_none_content(self):
        assert split_desc(None) == ("", "")


class TestDescRelativePath:
    """测试约定相对路径推导."""

    def test_returns_convention_path(self):
        assert desc_relative_path("abc12345") == "files/desc/abc12345.desc.md"


class TestDescAbsPath:
    """测试绝对路径推导."""

    def test_returns_abs_under_user_base(self, mock_user_base):
        path = desc_abs_path("user1", "abc12345")
        assert path == mock_user_base / "files/desc/abc12345.desc.md"


class TestWriteDesc:
    """测试描述文件写入."""

    def test_writes_content(self, mock_user_base):
        write_desc("user1", "abc12345", "一只橘猫在阳光下")
        path = mock_user_base / "files/desc/abc12345.desc.md"
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "一只橘猫在阳光下"

    def test_empty_content_no_write(self, mock_user_base):
        write_desc("user1", "abc12345", "")
        path = mock_user_base / "files/desc/abc12345.desc.md"
        assert not path.exists()

    def test_creates_parent_directory(self, mock_user_base):
        write_desc("user1", "abc12345", "内容")
        assert (mock_user_base / "files/desc").is_dir()

    def test_failure_does_not_raise(self, mock_user_base):
        """最佳努力: 写入失败仅日志, 不抛异常."""
        with patch("src.files.desc_writer.Path.write_text", side_effect=OSError("disk full")):
            # 不应抛异常
            write_desc("user1", "abc12345", "内容")


class TestReadDesc:
    """测试描述文件读取."""

    def test_reads_existing(self, mock_user_base):
        write_desc("user1", "abc12345", "描述内容")
        assert read_desc("user1", "abc12345") == "描述内容"

    def test_returns_none_when_missing(self, mock_user_base):
        assert read_desc("user1", "nonexist") is None


class TestDeleteDesc:
    """测试描述文件删除."""

    def test_deletes_existing(self, mock_user_base):
        write_desc("user1", "abc12345", "内容")
        assert delete_desc("user1", "abc12345") is True
        assert not (mock_user_base / "files/desc/abc12345.desc.md").exists()

    def test_returns_false_when_missing(self, mock_user_base):
        assert delete_desc("user1", "nonexist") is False

    def test_overwrite_on_rewrite(self, mock_user_base):
        """重复写入应覆盖旧内容 (后台摘要覆盖临时摘要)."""
        write_desc("user1", "abc12345", "临时摘要")
        write_desc("user1", "abc12345", "LLM 最终摘要")
        assert read_desc("user1", "abc12345") == "LLM 最终摘要"
