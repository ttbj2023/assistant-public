"""chat 路由消息内容提取测试.

覆盖 _extract_message_content: text/image_url/file 三类 block 的提取与容错.
file block 为纯文本文档入口 (md/txt), 与 image 平行产出 document_datas.

注意: 未知 type 与 file 字段缺失在 pydantic 验证层 (422) 被拒,
函数级 ValueError 仅覆盖"通过验证但语义无效"的情况 (空内容等).
"""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from src.api.routes.chat import _extract_message_content
from src.core.types import ContentBlock

B64_IMG = base64.b64encode(b"\x89PNG fake").decode()


def text_block(text: str) -> ContentBlock:
    return ContentBlock(type="text", text=text)


def image_block() -> ContentBlock:
    return ContentBlock(
        type="image_url",
        image_url={"url": f"data:image/png;base64,{B64_IMG}"},
    )


def file_block(filename: str, content: str) -> ContentBlock:
    return ContentBlock(type="file", file={"filename": filename, "content": content})


def binary_file_block(filename: str, content_b64: str) -> ContentBlock:
    return ContentBlock(
        type="file",
        file={"filename": filename, "content_b64": content_b64},
    )


class TestExtractMessageContent:
    """测试_extract_message_content - 多模态内容提取."""

    def test_plain_string_returns_text_only(self):
        """纯字符串内容: 原样返回, 无图片无文档."""
        text, images, documents = _extract_message_content("你好")
        assert text == "你好"
        assert images == []
        assert documents == []

    def test_text_blocks_joined_by_newline(self):
        """多个 text block 以换行拼接."""
        text, _, _ = _extract_message_content([
            text_block("第一段"),
            text_block("第二段"),
        ])
        assert text == "第一段\n第二段"

    def test_image_url_block_extracted_as_base64_data(self):
        """image_url block: data URL 解码为 bytes."""
        _, images, documents = _extract_message_content([
            text_block("看图"),
            image_block(),
        ])
        assert len(images) == 1
        assert images[0]["data"] == b"\x89PNG fake"
        assert images[0]["mime_type"] == "image/png"
        assert documents == []

    def test_file_block_extracted_as_document(self):
        """file block: 提取 filename + 纯文本 content."""
        text, images, documents = _extract_message_content([
            text_block("总结这个文件"),
            file_block("报告.md", "# 标题\n正文"),
        ])
        assert text == "总结这个文件"
        assert images == []
        assert documents == [{"filename": "报告.md", "content": "# 标题\n正文"}]

    def test_multiple_file_blocks_preserved_in_order(self):
        """多个 file block 按顺序提取."""
        _, _, documents = _extract_message_content([
            file_block("a.md", "A"),
            file_block("b.txt", "B"),
        ])
        assert [d["filename"] for d in documents] == ["a.md", "b.txt"]

    def test_mixed_blocks_all_extracted(self):
        """混合 text/image/file 全部提取."""
        text, images, documents = _extract_message_content([
            text_block("说明"),
            image_block(),
            file_block("x.md", "X"),
        ])
        assert text == "说明"
        assert len(images) == 1
        assert len(documents) == 1

    def test_binary_file_block_extracted_with_content_b64(self):
        """file block (content_b64 形态): 原样传递 filename + content_b64."""
        b64 = base64.b64encode(b"\x50\x4b binary").decode()
        _, _, documents = _extract_message_content([
            binary_file_block("报告.docx", b64),
        ])
        assert documents == [{"filename": "报告.docx", "content_b64": b64}]

    def test_mixed_text_and_binary_file_blocks(self):
        """纯文本与二进制 file block 混合, 各自形态保留."""
        b64 = base64.b64encode(b"pdf-bytes").decode()
        _, _, documents = _extract_message_content([
            file_block("a.md", "A"),
            binary_file_block("b.pdf", b64),
        ])
        assert documents[0] == {"filename": "a.md", "content": "A"}
        assert documents[1] == {"filename": "b.pdf", "content_b64": b64}

    def test_binary_file_block_without_any_content_rejected(self):
        """file block 两个字段都缺: pydantic 验证层拒绝."""
        with pytest.raises(ValidationError):
            ContentBlock.model_validate({"type": "file", "file": {"filename": "a.pdf"}})

    def test_invalid_image_url_ignored(self):
        """非法 image_url (非 data URL) 忽略不报错."""
        _, images, _ = _extract_message_content([
            ContentBlock(
                type="image_url",
                image_url={"url": "https://example.com/x.png"},
            )
        ])
        assert images == []

    def test_empty_file_content_raises_value_error(self):
        """file block content 为空串: 通过验证但语义无效, 函数级拒绝."""
        with pytest.raises(ValueError, match="无效的 file 内容块"):
            _extract_message_content([file_block("a.md", "")])

    def test_whitespace_filename_raises_value_error(self):
        """file block filename 空白: 函数级拒绝."""
        with pytest.raises(ValueError, match="无效的 file 内容块"):
            _extract_message_content([file_block("   ", "内容")])

    def test_unsupported_content_type_raises(self):
        """不支持的 content 类型抛 ValueError."""
        with pytest.raises(ValueError, match="不支持的内容类型"):
            _extract_message_content(123)  # type: ignore[arg-type]

    def test_unknown_block_type_rejected_by_validation(self):
        """未知 block type 在 pydantic 验证层被拒 (API 层 422)."""
        with pytest.raises(ValidationError):
            ContentBlock.model_validate({"type": "audio", "audio": {"url": "x"}})

    def test_file_block_missing_content_rejected_by_validation(self):
        """file block 缺 content 在 pydantic 验证层被拒."""
        with pytest.raises(ValidationError):
            ContentBlock.model_validate({"type": "file", "file": {"filename": "a.md"}})
