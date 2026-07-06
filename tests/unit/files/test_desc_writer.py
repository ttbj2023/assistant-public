"""desc_writer 单元测试.

测试 .desc.md 描述文件的约定路径推导、写入、读取、删除, 以及最佳努力的异常隔离.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.files.desc_writer import (
    delete_desc,
    desc_abs_path,
    desc_relative_path,
    read_desc,
    write_desc,
)


@pytest.fixture
def mock_user_base(tmp_path: Path):
    """mock user_base_path 指向临时目录."""
    resolver = MagicMock()
    resolver.get_user_base_path.return_value = tmp_path
    with patch("src.files.desc_writer.get_user_path_resolver", return_value=resolver):
        yield tmp_path


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
