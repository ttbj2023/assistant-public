"""ExportDocument 外部工具编排集成测试.

验证 export_document 服务层的完整渲染编排和 exported_files 反馈链路,
补充单元测试(test_file_output)未覆盖的跨组件协作场景:

- DOCX 渲染管线: run_export_document → _render_docx → _run_pandoc → register_tool_output
- PDF 渲染管线: 额外的 markdown → HTML → Chromium → PDF 环节
- exported_files ContextVar 反馈: register_tool_output → ctx.exported_files.append
- 错误降级: 非法文件名返回结构化错误

测试策略: 灰盒 - Mock 外部进程(pandoc/Chromium)和 DB 依赖(去重/配额/签名URL配置),
保留真实编排逻辑(路径解析/文件写入/UserContext 透传).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

import pytest

from src.core.context import (
    UserContext,
    get_user_context,
    reset_user_context,
    set_user_context,
)


@contextmanager
def _patch_register_deps() -> Iterator[None]:
    """Mock register_tool_output 的外部依赖(去重/签名URL/配置/附件DB), 避免 DB 初始化."""
    mock_storage_config = MagicMock()
    mock_storage_config.file_store = MagicMock(
        deduplication_enabled=True,
        quota_check_enabled=False,
    )
    mock_provider = MagicMock()
    mock_provider.compose_token.return_value = ("1700000000", "sig_hex")
    mock_api_config = MagicMock()
    mock_api_config.get_file_server_url.return_value = (
        "http://localhost:8000/v1/files/dl"
    )

    targets = {
        "src.config.storage_config.get_config": mock_storage_config,
        "src.files.signed_url.get_signed_url_provider": mock_provider,
        "src.config.api_config.get_config": mock_api_config,
    }
    with ExitStack() as stack:
        for target, return_value in targets.items():
            stack.enter_context(patch(target, return_value=return_value))
        yield


@pytest.mark.integration
class TestExportDocumentOrchestrationIntegration:
    """ExportDocument 渲染管线和 exported_files 反馈集成测试."""

    @pytest.mark.asyncio
    async def test_docx_pipeline_exported_files_feedback(self):
        """测试 DOCX 渲染管线 + exported_files ContextVar 反馈 + document_meta.

        协作场景: run_export_document → _render_docx → _run_pandoc → register_tool_output → ctx.exported_files
        设计思路: 用真实 set_user_context + 真实路径解析, Mock pandoc/DB依赖,
                  验证完整编排链和 exported_files 回填
        业务价值: 确保文件产出后对话历史能获取文件URL(exported_files 是 chat.py 替换标记的数据源)
        """
        import json

        from src.tools.external.export_document import service as ed_service

        ctx = UserContext(user_id="testuser", thread_id="main", agent_id="test-agent")
        token = set_user_context(ctx)

        async def fake_pandoc(input_path, output_path, output_format, **kwargs):
            output_path.write_bytes(b"fake docx content")

        try:
            with (
                _patch_register_deps(),
                patch.object(ed_service, "_run_pandoc", side_effect=fake_pandoc),
                patch.object(ed_service, "schedule_summary_generation"),
            ):
                result = await ed_service.run_export_document(
                    content="# 测试文档\n正文内容",
                    style="default",
                    output_format="docx",
                    filename="test_report",
                    user_id="testuser",
                    thread_id="main",
                )

            assert result["success"] is True
            assert result["format"] == "docx"
            assert result["size_bytes"] > 0

            exported = get_user_context().exported_files
            assert len(exported) == 1, "exported_files 应包含 1 个文件记录"
            entry = exported[0]
            assert entry["format"] == "docx"
            assert entry["file_type"] == "document"
            assert entry["brief"] == "正文内容"
            assert "test_report" in entry["filename"]
            assert entry["url"].startswith("http://localhost:8000/v1/files/dl/")
            assert entry["internal_path"].startswith("files/exports/")

            doc_meta = json.loads(entry["document_meta"])
            assert doc_meta["summary"] == "正文内容"
            assert doc_meta["format"] == "docx"
            assert len(doc_meta["toc"]) == 1
            assert doc_meta["toc"][0]["title"] == "测试文档"
        finally:
            reset_user_context(token)

    @pytest.mark.asyncio
    async def test_pdf_pipeline_orchestration(self):
        """测试 PDF 渲染管线编排 + document_meta 生成.

        协作场景: _render_pdf → _run_pandoc(md→html) → build_html_document → get_browser_renderer().render_to_pdf
        设计思路: Mock pandoc(写假HTML)和Chromium(写假PDF), 验证编排顺序产出正确
        业务价值: 确保 PDF 渲染管线正确串联 3 个步骤, document_meta 包含正确的目录信息
        """
        import json

        from src.tools.external.export_document import service as ed_service

        ctx = UserContext(user_id="testuser", thread_id="main", agent_id="test-agent")
        token = set_user_context(ctx)

        async def fake_pandoc_html(input_path, output_path, output_format, **kwargs):
            output_path.write_text("<h1>标题</h1><p>正文</p>", encoding="utf-8")

        mock_renderer = MagicMock()

        async def fake_render_to_pdf(html, output_path):
            output_path.write_bytes(b"%PDF-1.4 fake pdf")

        mock_renderer.render_to_pdf = fake_render_to_pdf

        try:
            with (
                _patch_register_deps(),
                patch.object(ed_service, "_run_pandoc", side_effect=fake_pandoc_html),
                patch.object(
                    ed_service, "get_browser_renderer", return_value=mock_renderer
                ),
                patch.object(ed_service, "schedule_summary_generation"),
            ):
                result = await ed_service.run_export_document(
                    content="# PDF测试\n段落",
                    style="academic",
                    output_format="pdf",
                    filename="pdf_report",
                    user_id="testuser",
                    thread_id="main",
                )

            assert result["success"] is True
            assert result["format"] == "pdf"
            assert result["size_bytes"] > 0

            exported = get_user_context().exported_files
            assert len(exported) == 1
            assert exported[0]["format"] == "pdf"

            doc_meta = json.loads(exported[0]["document_meta"])
            assert doc_meta["format"] == "pdf"
            assert len(doc_meta["toc"]) == 1
            assert doc_meta["toc"][0]["title"] == "PDF测试"
        finally:
            reset_user_context(token)

    @pytest.mark.asyncio
    async def test_invalid_filename_returns_structured_error(self):
        """测试非法文件名: 返回结构化错误, 不抛异常.

        协作场景: _validate_filename → ValueError → run_export_document try/except
        设计思路: 传入路径遍历文件名, 验证返回 success=False 的结构化错误
        业务价值: 防止路径遍历攻击, 错误信息透传给 LLM
        """
        from src.tools.external.export_document import service as ed_service

        result = await ed_service.run_export_document(
            content="内容",
            style="default",
            output_format="pdf",
            filename="../../etc/passwd",
            user_id="testuser",
            thread_id="main",
        )

        assert result["success"] is False
        assert "error" in result
