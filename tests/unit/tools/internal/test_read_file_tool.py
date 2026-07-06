"""ReadFileTool 单元测试.

测试文件描述读取工具: .desc.md 读取 + truncated 标志.
Mock 外部依赖: desc_writer.read_desc, 附件注册表.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.files import AttachmentDTO
from src.tools.internal.read_file_tool import ReadFileTool


@pytest.fixture
def tool() -> ReadFileTool:
    return ReadFileTool(user_id="u1", thread_id="t1", agent_id="a1")


def make_entry(**overrides) -> AttachmentDTO:
    defaults = {
        "file_id": "abc12345",
        "file_type": "image",
        "internal_path": "files/images/test.jpg",
        "filename": "test.jpg",
        "brief": "测试图片",
        "detail": "DB 里的旧描述",
        "file_format": "jpg",
        "file_size": 1024,
        "round_number": 1,
    }
    defaults.update(overrides)
    return AttachmentDTO(**defaults)


class TestArun:
    """测试异步执行."""

    @pytest.mark.asyncio
    async def test_read_desc_file_success(self, tool):
        """.desc.md 有内容时优先返回, source=desc_file."""
        entry = make_entry()
        with (
            patch(
                "src.files.desc_writer.read_desc",
                return_value="一只橘猫在阳光下",
            ),
            patch.object(tool, "_get_entry", return_value=entry),
        ):
            result = await tool._arun(file_id="abc12345")

        data = json.loads(result)
        assert data["success"] is True
        assert data["content"] == "一只橘猫在阳光下"
        assert data["source"] == "desc_file"
        assert data["file_id"] == "abc12345"
        assert data["filename"] == "test.jpg"

    @pytest.mark.asyncio
    async def test_no_content_returns_error(self, tool):
        """.desc.md 无内容且 DB 无元信息时返回错误."""
        entry = make_entry()
        with (
            patch("src.files.desc_writer.read_desc", return_value=None),
            patch.object(tool, "_get_entry", return_value=entry),
        ):
            result = await tool._arun(file_id="abc12345")

        data = json.loads(result)
        assert data["success"] is False
        assert "无可用描述" in data["error"]

    @pytest.mark.asyncio
    async def test_entry_not_found_with_desc_still_works(self, tool):
        """DB entry 不存在但 .desc.md 有内容时仍返回 (元信息为 None)."""
        with (
            patch(
                "src.files.desc_writer.read_desc",
                return_value="描述内容",
            ),
            patch.object(tool, "_get_entry", return_value=None),
        ):
            result = await tool._arun(file_id="abc12345")

        data = json.loads(result)
        assert data["success"] is True
        assert data["content"] == "描述内容"
        assert data["filename"] is None

    @pytest.mark.asyncio
    async def test_max_chars_truncation(self, tool):
        """超长描述应截断到 max_chars, 并标记 truncated."""
        long_content = "x" * 5000
        entry = make_entry()
        with (
            patch("src.files.desc_writer.read_desc", return_value=long_content),
            patch.object(tool, "_get_entry", return_value=entry),
        ):
            result = await tool._arun(file_id="abc12345", max_chars=100)

        data = json.loads(result)
        assert len(data["content"]) == 100
        assert data["truncated"] is True
        assert data["content_total_chars"] == 5000

    @pytest.mark.asyncio
    async def test_no_truncation_when_within_limit(self, tool):
        """描述未超 max_chars 时 truncated=False."""
        entry = make_entry()
        with (
            patch("src.files.desc_writer.read_desc", return_value="短描述"),
            patch.object(tool, "_get_entry", return_value=entry),
        ):
            result = await tool._arun(file_id="abc12345", max_chars=4000)

        data = json.loads(result)
        assert data["truncated"] is False
        assert data["content_total_chars"] == len("短描述")

    @pytest.mark.asyncio
    async def test_exception_returns_error(self, tool):
        """异常时返回错误格式."""
        with patch.object(
            tool, "_get_entry", side_effect=Exception("unexpected error")
        ):
            result = await tool._arun(file_id="abc12345")

        data = json.loads(result)
        assert data["success"] is False
        assert "unexpected error" in data["error"]
