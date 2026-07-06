"""register_tool_output 路径推导测试.

验证 register_tool_output 从 output_path 动态推导 internal_path,
而非硬编码 files/exports/, 确保子目录文件 (如 charts/) 可正确下载.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from src.core.path_resolver import get_user_path_resolver
from src.tools.shared.file_output import register_tool_output


def _make_storage_config(dedup: bool = True, quota: bool = False) -> MagicMock:
    """创建 mock FileStoreConfig."""
    storage_config = MagicMock()
    storage_config.file_store = MagicMock(
        deduplication_enabled=dedup,
        quota_check_enabled=quota,
    )
    return storage_config


def _make_mock_ctx(user_id: str, thread_id: str) -> MagicMock:
    """创建 mock 用户上下文."""
    ctx = MagicMock()
    ctx.user_id = user_id
    ctx.thread_id = thread_id
    ctx.agent_id = "personal-assistant"
    ctx.round_number = 1
    ctx.exported_files = []
    return ctx


def _apply_register_patches(
    stack: ExitStack,
    storage_config: MagicMock,
    mock_ctx: MagicMock,
) -> None:
    """在 ExitStack 上注册 register_tool_output 所需的全部 patch.

    不 mock 路径解析器/文件注册表, 使用真实 resolver+registry 确保 register 和 resolve 一致.
    测试间通过不同文件内容 (filename.encode) 避免去重命中.
    """
    mock_provider = MagicMock()
    mock_provider.compose_token.return_value = ("1700000000", "sig_hex")

    mock_api_config = MagicMock()
    mock_api_config.get_file_server_url.return_value = "http://localhost:8000"

    targets = {
        "src.core.context.get_user_context": mock_ctx,
        "src.config.storage_config.get_config": storage_config,
        "src.files.signed_url.get_signed_url_provider": mock_provider,
        "src.config.api_config.get_config": mock_api_config,
    }
    for target, return_value in targets.items():
        stack.enter_context(patch(target, return_value=return_value))


async def _run_register_tool_output(
    subdir: str,
    filename: str,
    output_format: str,
    file_type: str,
) -> dict:
    """使用真实路径解析器创建文件并调用 register_tool_output.

    文件内容用 filename.encode() 保证不同测试 content_hash 不同 (避免用户级
    FileRegistry 跨测试去重命中).
    """
    user_id = "testuser"
    thread_id = "main"
    resolver = get_user_path_resolver()

    file_dir = resolver.get_shared_storage_path(user_id, thread_id, subdir)
    output_path = file_dir / filename
    output_path.write_bytes(filename.encode())

    mock_ctx = _make_mock_ctx(user_id, thread_id)

    with ExitStack() as stack:
        _apply_register_patches(stack, _make_storage_config(), mock_ctx)
        return await register_tool_output(
            output_path=output_path,
            display_filename=f"display.{output_format}",
            output_filename=filename,
            output_format=output_format,
            file_type=file_type,
            content="test content",
            summary=None,
            user_id=user_id,
            thread_id=thread_id,
        )


class TestRegisterToolOutputPathDerivation:
    """测试 register_tool_output 的路径推导逻辑."""

    @pytest.mark.asyncio
    async def test_chart_subdirectory_derived_correctly(self) -> None:
        """图表文件写入 charts/ 子目录时, physical_path 应包含 charts/."""
        filename = "chart_20260607_120000_abcd1234.png"
        result = await _run_register_tool_output(
            subdir="files/exports/charts",
            filename=filename,
            output_format="png",
            file_type="image",
        )

        assert result["success"] is True

        from src.core.path_resolver import resolve_attachment_internal_path
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        registry = await create_file_registry_service("testuser")
        entry = await registry.get(result["file_id"])
        assert entry is not None
        internal_path = entry.physical_path.split("shared/", 1)[-1]
        assert internal_path == f"files/exports/charts/{filename}"

        resolved = resolve_attachment_internal_path(internal_path, "testuser", "main")
        assert resolved.exists()
        assert resolved.name == filename

    @pytest.mark.asyncio
    async def test_document_exports_path_backward_compatible(self) -> None:
        """文档文件直接写入 exports/ 时, physical_path 应为 files/exports/xxx."""
        filename = "report_20260607_120000_abcd1234.pdf"
        result = await _run_register_tool_output(
            subdir="files/exports",
            filename=filename,
            output_format="pdf",
            file_type="document",
        )

        assert result["success"] is True

        from src.core.path_resolver import resolve_attachment_internal_path
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        registry = await create_file_registry_service("testuser")
        entry = await registry.get(result["file_id"])
        assert entry is not None
        internal_path = entry.physical_path.split("shared/", 1)[-1]
        assert internal_path == f"files/exports/{filename}"

        resolved = resolve_attachment_internal_path(internal_path, "testuser", "main")
        assert resolved.exists()
        assert resolved.name == filename


class TestBriefParameter:
    """brief 参数优先级: brief > summary > _compose_brief."""

    @staticmethod
    async def _register_with_brief(
        filename: str, content: bytes, *, brief: str | None, summary: str | None
    ) -> str:
        """注册文件并返回 file_id (真实 resolver+registry)."""
        user_id = "testuser"
        thread_id = "main"
        resolver = get_user_path_resolver()
        file_dir = resolver.get_shared_storage_path(
            user_id, thread_id, "files/exports"
        )
        output_path = file_dir / filename
        output_path.write_bytes(content)

        mock_ctx = _make_mock_ctx(user_id, thread_id)
        with ExitStack() as stack:
            _apply_register_patches(stack, _make_storage_config(), mock_ctx)
            result = await register_tool_output(
                output_path=output_path,
                display_filename="display.pdf",
                output_filename=filename,
                output_format="pdf",
                file_type="document",
                content="test content",
                summary=summary,
                user_id=user_id,
                thread_id=thread_id,
                brief=brief,
            )
        return result["file_id"]

    @pytest.mark.asyncio
    async def test_brief_overrides_summary(self) -> None:
        """传 brief 时, entry.brief = brief (优先于 summary)."""
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        file_id = await self._register_with_brief(
            "brief_ovr_20260607_120000_aaaa1111.pdf",
            b"brief-override-content",
            brief="自定义brief标签",
            summary="摘要内容",
        )

        registry = await create_file_registry_service("testuser")
        entry = await registry.get(file_id)
        assert entry is not None
        assert entry.brief == "自定义brief标签"

    @pytest.mark.asyncio
    async def test_summary_fallback_when_no_brief(self) -> None:
        """不传 brief 传 summary 时, entry.brief = summary (向后兼容)."""
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        file_id = await self._register_with_brief(
            "sum_fb_20260607_120000_bbbb2222.pdf",
            b"summary-fallback-content",
            brief=None,
            summary="文档摘要",
        )

        registry = await create_file_registry_service("testuser")
        entry = await registry.get(file_id)
        assert entry is not None
        assert entry.brief == "文档摘要"
