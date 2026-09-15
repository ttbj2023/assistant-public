"""ReadFileTool 单元测试.

测试文件描述读取工具: 摘要模式 (默认) + 行级分页模式.
Mock 外部依赖: desc_writer.read_desc, 附件注册表.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.files import AttachmentDTO
from src.tools.internal.read_file_tool import ReadFileTool
from src.tools.shared.tool_runtime import inject_identity


@pytest.fixture
def tool() -> ReadFileTool:
    tool = ReadFileTool()
    inject_identity(tool, "u1", "t1", "a1")
    return tool


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


def make_desc(summary: str, original: str) -> str:
    """构造统一结构 desc 内容."""
    from src.files.desc_writer import compose_desc

    return compose_desc(summary, original)


class TestSummaryMode:
    """摘要模式: 默认 (仅传 file_id) 返回摘要区 + 原文入口行号."""

    @pytest.mark.asyncio
    async def test_summary_mode_returns_summary_and_original_start(self, tool):
        """默认调用返回摘要与原文起始行号."""
        desc = make_desc("一张橘猫照片", "整体描述\n文字转录1\n文字转录2")
        entry = make_entry()
        with (
            patch("src.files.desc_writer.read_desc", return_value=desc),
            patch.object(tool, "_get_entry", return_value=entry),
        ):
            result = await tool._arun(file_id="abc12345")

        data = json.loads(result)
        assert data["success"] is True
        assert data["mode"] == "summary"
        assert data["summary"] == "一张橘猫照片"
        assert data["has_original"] is True
        # desc 行: [摘要, 空, ---, 空, 整体描述, 文字转录1, 文字转录2] → 原文从第5行
        assert data["original_start_line"] == 5
        assert data["total_lines"] == 7

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
        assert data["mode"] == "summary"
        assert data["filename"] is None

    @pytest.mark.asyncio
    async def test_legacy_desc_falls_back_to_brief(self, tool):
        """旧格式 desc (无分隔符): summary 回退 entry.brief, 原文从第1行."""
        entry = make_entry(brief="测试图片")
        with (
            patch("src.files.desc_writer.read_desc", return_value="旧格式全文"),
            patch.object(tool, "_get_entry", return_value=entry),
        ):
            result = await tool._arun(file_id="abc12345")

        data = json.loads(result)
        assert data["success"] is True
        assert data["summary"] == "测试图片"
        assert data["original_start_line"] == 1
        assert data["note"] is not None

    @pytest.mark.asyncio
    async def test_no_original_marks_has_original_false(self, tool):
        """新格式纯摘要 (分隔符+空原文区): has_original=false, original_start_line=null."""
        entry = make_entry()
        with (
            patch(
                "src.files.desc_writer.read_desc",
                return_value=make_desc("仅一句摘要", ""),
            ),
            patch.object(tool, "_get_entry", return_value=entry),
        ):
            result = await tool._arun(file_id="abc12345")

        data = json.loads(result)
        assert data["has_original"] is False
        assert data["original_start_line"] is None


class TestLineMode:
    """行级模式: start_line + max_lines 窗口读取 + next_start_line 续读."""

    @pytest.mark.asyncio
    async def test_window_slice_and_next_start_line(self, tool):
        """中段窗口: 返回指定行区间, has_more=true 给出续读行号."""
        original = "\n".join(f"line{i}" for i in range(1, 11))  # 10行原文
        desc = make_desc("摘要", original)
        # desc 行: [摘要, 空, ---, 空, line1..line10] → 原文从第5行
        entry = make_entry()
        with (
            patch("src.files.desc_writer.read_desc", return_value=desc),
            patch.object(tool, "_get_entry", return_value=entry),
        ):
            result = await tool._arun(file_id="abc12345", start_line=5, max_lines=3)

        data = json.loads(result)
        assert data["success"] is True
        assert data["mode"] == "lines"
        assert data["content"] == "line1\nline2\nline3"
        assert data["returned_lines"] == 3
        assert data["start_line"] == 5
        assert data["end_line"] == 7
        assert data["next_start_line"] == 8
        assert data["has_more"] is True
        assert data["total_lines"] == 14

    @pytest.mark.asyncio
    async def test_last_window_closes_pagination(self, tool):
        """尾页: 读到文件末尾 has_more=false, next_start_line=null."""
        original = "a\nb\nc"
        desc = make_desc("摘要", original)
        entry = make_entry()
        with (
            patch("src.files.desc_writer.read_desc", return_value=desc),
            patch.object(tool, "_get_entry", return_value=entry),
        ):
            result = await tool._arun(file_id="abc12345", start_line=5, max_lines=10)

        data = json.loads(result)
        assert data["content"] == "a\nb\nc"
        assert data["has_more"] is False
        assert data["next_start_line"] is None
        assert data["end_line"] == data["total_lines"]

    @pytest.mark.asyncio
    async def test_start_line_beyond_end_returns_empty_window(self, tool):
        """越界 start_line: 不报错, 返回空窗口 + note 提示."""
        desc = make_desc("摘要", "a\nb")
        entry = make_entry()
        with (
            patch("src.files.desc_writer.read_desc", return_value=desc),
            patch.object(tool, "_get_entry", return_value=entry),
        ):
            result = await tool._arun(file_id="abc12345", start_line=999)

        data = json.loads(result)
        assert data["success"] is True
        assert data["content"] == ""
        assert data["has_more"] is False
        assert data["note"] is not None

    @pytest.mark.asyncio
    async def test_char_limit_stops_early_with_note(self, tool, monkeypatch):
        """累计字符超内部上限: 提前停窗, note 说明."""
        import src.tools.internal.read_file_tool as mod

        monkeypatch.setattr(mod, "_MAX_RESPONSE_CHARS", 20)
        # 每行6字符: 第1行累计7, 第2行14, 第3行21超限 → 只返回2行
        desc = make_desc("摘要", "\n".join(["aaaaaa"] * 4))
        entry = make_entry()
        with (
            patch("src.files.desc_writer.read_desc", return_value=desc),
            patch.object(tool, "_get_entry", return_value=entry),
        ):
            result = await tool._arun(file_id="abc12345", start_line=5, max_lines=100)

        data = json.loads(result)
        assert data["success"] is True
        assert data["note"] is not None
        assert "字符上限" in data["note"]
        assert data["has_more"] is True
        assert data["returned_lines"] == 2

    @pytest.mark.asyncio
    async def test_single_huge_line_hard_truncated(self, tool, monkeypatch):
        """病态单行 (整行超上限): 硬截断 + 标记 + note."""
        import src.tools.internal.read_file_tool as mod

        monkeypatch.setattr(mod, "_MAX_RESPONSE_CHARS", 20)
        desc = make_desc("摘要", "x" * 100)
        entry = make_entry()
        with (
            patch("src.files.desc_writer.read_desc", return_value=desc),
            patch.object(tool, "_get_entry", return_value=entry),
        ):
            result = await tool._arun(file_id="abc12345", start_line=5)

        data = json.loads(result)
        assert data["success"] is True
        assert mod._LINE_TRUNCATION_MARK in data["content"]
        assert data["note"] is not None

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
