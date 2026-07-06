"""AnalyzeImageTool 单元测试."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.files import AttachmentDTO
from src.tools.internal.analyze_image_tool import AnalyzeImageTool


@pytest.fixture
def tool() -> AnalyzeImageTool:
    return AnalyzeImageTool(user_id="u1", thread_id="t1", agent_id="a1")


def make_entry(**overrides: Any) -> AttachmentDTO:
    defaults = {
        "file_id": "abc12345",
        "file_type": "image",
        "internal_path": "files/images/test.jpg",
        "filename": "test.jpg",
        "brief": "图片",
        "detail": "",
        "file_format": "jpg",
        "file_size": 1024,
        "round_number": 3,
    }
    defaults.update(overrides)
    return AttachmentDTO(**defaults)


def parse_result(result: str) -> dict:
    return json.loads(result)


class TestAnalyzeImageTool:
    """测试按需读图工具."""

    @pytest.mark.asyncio
    async def test_reads_specified_image(
        self,
        tool: AnalyzeImageTool,
        tmp_path: Path,
    ) -> None:
        image_dir = tmp_path / "shared" / "files" / "images"
        image_dir.mkdir(parents=True)
        (image_dir / "test.jpg").write_bytes(b"image-bytes")
        entry = make_entry()

        resolver = MagicMock()
        resolver.get_thread_base_path.return_value = tmp_path

        with (
            patch.object(tool, "_get_entry", return_value=entry),
            patch.object(tool, "_read_image", return_value=("识别结果", "m1")),
            patch(
                "src.core.path_resolver.get_user_path_resolver",
                return_value=resolver,
            ),
        ):
            result = await tool._arun(
                attachment_id="abc12345",
                prompt="读文字",
            )

        data = parse_result(result)
        assert data["success"] is True
        assert data["attachment_id"] == "abc12345"
        assert data["round_number"] == 3
        assert data["result"] == "识别结果"

    @pytest.mark.asyncio
    async def test_uses_recent_image_when_attachment_id_missing(
        self,
        tool: AnalyzeImageTool,
        tmp_path: Path,
    ) -> None:
        image_dir = tmp_path / "shared" / "files" / "images"
        image_dir.mkdir(parents=True)
        (image_dir / "test.jpg").write_bytes(b"image-bytes")
        entry = make_entry()

        resolver = MagicMock()
        resolver.get_thread_base_path.return_value = tmp_path

        with (
            patch.object(tool, "_get_recent_image", return_value=entry) as mock_recent,
            patch.object(tool, "_read_image", return_value=("ok", "m1")),
            patch(
                "src.core.path_resolver.get_user_path_resolver",
                return_value=resolver,
            ),
        ):
            result = await tool._arun(prompt="看刚才那张", recent_index=1)

        data = parse_result(result)
        assert data["success"] is True
        mock_recent.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_rejects_non_image_attachment(
        self,
        tool: AnalyzeImageTool,
    ) -> None:
        entry = make_entry(file_type="document")

        with patch.object(tool, "_get_entry", return_value=entry):
            result = await tool._arun(
                attachment_id="abc12345",
                prompt="读图",
            )

        assert "不是图片" in result

    @pytest.mark.asyncio
    async def test_missing_file_returns_error(
        self,
        tool: AnalyzeImageTool,
        tmp_path: Path,
    ) -> None:
        entry = make_entry()
        resolver = MagicMock()
        resolver.get_thread_base_path.return_value = tmp_path

        with (
            patch.object(tool, "_get_entry", return_value=entry),
            patch(
                "src.core.path_resolver.get_user_path_resolver",
                return_value=resolver,
            ),
        ):
            result = await tool._arun(
                attachment_id="abc12345",
                prompt="读图",
            )

        assert "已不存在" in result
