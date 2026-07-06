"""RegenerateDownloadLinkTool 单元测试.

测试重新生成下载链接工具的业务逻辑: 链接生成, 文件验证.
Mock外部依赖: 附件注册表, signed_url_provider, path_resolver.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.files import AttachmentDTO
from src.tools.internal.regenerate_download_link_tool import RegenerateDownloadLinkTool


@pytest.fixture
def tool():
    return RegenerateDownloadLinkTool(user_id="u1", thread_id="t1", agent_id="a1")


def make_entry(**overrides) -> AttachmentDTO:
    defaults = {
        "file_id": "abc12345",
        "file_type": "document",
        "internal_path": "shared/files/exports/test.pdf",
        "filename": "test.pdf",
        "brief": "test file",
        "detail": "",
        "file_format": "pdf",
        "file_size": 2048,
        "round_number": 1,
    }
    defaults.update(overrides)
    return AttachmentDTO(**defaults)


class TestArun:
    """测试异步执行."""

    @pytest.mark.asyncio
    async def test_attachment_not_found(self, tool):
        with patch.object(tool, "_get_entry", return_value=None):
            result = await tool._arun(file_id="nonexist")

        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_file_no_longer_exists(self, tool, tmp_path):
        entry = make_entry(internal_path="nonexistent/file.pdf")

        nonexistent_path = tmp_path / "nonexistent" / "file.pdf"

        with (
            patch.object(tool, "_get_entry", return_value=entry),
            patch(
                "src.core.path_resolver.resolve_attachment_internal_path",
                return_value=nonexistent_path,
            ),
        ):
            result = await tool._arun(file_id="abc12345")

        assert "已不存在" in result

    @pytest.mark.asyncio
    async def test_successful_link_generation(self, tool, tmp_path):
        file_path = tmp_path / "test.pdf"
        file_path.write_text("content")

        entry = make_entry(internal_path="files/exports/test.pdf")

        mock_provider = MagicMock()
        mock_provider.compose_token.return_value = "abc12345/2000000000/" + "a" * 32

        mock_api_config = MagicMock()
        mock_api_config.get_file_server_url.return_value = "http://localhost:8000"

        mock_ctx = MagicMock()
        mock_ctx.exported_files = []

        with (
            patch.object(tool, "_get_entry", return_value=entry),
            patch(
                "src.files.signed_url.get_signed_url_provider",
                return_value=mock_provider,
            ),
            patch(
                "src.core.path_resolver.resolve_attachment_internal_path",
                return_value=file_path,
            ),
            patch(
                "src.config.api_config.get_config",
                return_value=mock_api_config,
            ),
            patch(
                "src.core.context.get_user_context_or_none",
                return_value=mock_ctx,
            ),
        ):
            result = await tool._arun(file_id="abc12345")

        assert "下载链接已准备" in result
        assert "abc12345" in result
        assert "download_url" not in result
        assert len(mock_ctx.exported_files) == 1
        assert "u1/t1/a1" in mock_ctx.exported_files[0]["url"]

    @pytest.mark.asyncio
    async def test_exception_returns_error(self, tool):
        with patch.object(
            tool, "_get_entry", side_effect=Exception("unexpected error")
        ):
            result = await tool._arun(file_id="abc12345")

        assert "unexpected error" in result
