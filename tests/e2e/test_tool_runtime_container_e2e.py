"""tool-runtime 真容器契约 E2E 测试.

独特价值 (集成/单元测试无法覆盖):
- 集成层 mock 在 service 边界 (_run_pandoc/get_browser_renderer), 无法验证真容器
  的 pandoc/chromium/python 沙箱真实行为与 HTTP 端点契约
- 本测试直接调真实 service → 真实 tool-runtime 容器 (常驻), 验证真实渲染产物

环境约定: 开发环境 tool-runtime 容器常驻 (docker/tool-runtime/docker-compose.yml).
E2E 不管理容器生命周期 —— 探测 /health 不通即立即 skip 并报告启动命令, 不构建不等.
渲染产物真实 (PNG/DOCX/PDF), 文件注册侧复用 _patch_register_deps 隔离 (注册链已由
chart_maker / export_document 集成测试覆盖).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.config.runtime_env import get_tool_runtime_base_url
from src.core.context import UserContext, reset_user_context, set_user_context


@pytest.fixture(scope="session")
def tool_runtime_base_url():
    """探测 tool-runtime 容器; 不可用即整批 skip 并报告, 不构建不等待."""
    base_url = get_tool_runtime_base_url()
    try:
        resp = httpx.get(f"{base_url}/health", timeout=2.0)
        resp.raise_for_status()
        return base_url
    except Exception as e:
        pytest.skip(
            f"tool-runtime 容器不可用 ({base_url}): {e}. "
            "请启动: docker compose -f docker/tool-runtime/docker-compose.yml up -d",
        )


@contextmanager
def _patch_register_deps() -> Iterator[None]:
    """隔离 register_tool_output 的注册侧依赖 (注册链已由集成测试覆盖).

    本测试聚焦真容器渲染产物, 注册侧 mock 与 chart_maker/export_document 集成一致.
    """
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


@pytest.fixture
def user_context():
    """注入 UserContext (chart_maker/export_document 经 register_tool_output 需要)."""
    ctx = UserContext(
        user_id="e2e_user",
        thread_id="e2e_thread",
        agent_id="personal-assistant",
        round_number=1,
    )
    token = set_user_context(ctx)
    yield ctx
    reset_user_context(token)


@pytest.mark.e2e
class TestToolRuntimeContainerE2E:
    """tool-runtime 真容器端点契约 E2E 测试 (容器常驻, 不可用自动 skip)."""

    @pytest.mark.asyncio
    async def test_e2e_execute_python(self, tool_runtime_base_url):
        """PythonExecutorTool → 真 /execute: print(2+2) 返回 stdout=4.

        独特价值: 验证真实 python 沙箱执行 + 我们的 HTTP 调用契约.
        """
        import json

        from src.tools.external.python_executor_tool import PythonExecutorTool

        tool = PythonExecutorTool()
        result = json.loads(await tool._arun(code="print(2+2)"))

        assert result["success"] is True
        assert result["stdout"].strip() == "4"
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_e2e_render_chart_mermaid(self, tool_runtime_base_url, user_context):
        """ChartMaker → 真 /render/chart: mermaid 源码 → 合法 PNG.

        独特价值: 验证真实 Playwright + mermaid 渲染 + 我们的 service HTTP 调用契约.
        """
        from src.core.path_resolver import resolve_attachment_internal_path
        from src.tools.external.chart_maker import service as cm_service

        with _patch_register_deps():
            result = await cm_service.run_chart_maker(
                engine="mermaid",
                code="graph TD; A-->B;",
                filename="e2e_chart",
                title=None,
                user_id="e2e_user",
                thread_id="e2e_thread",
            )

        assert result["success"] is True, f"渲染失败: {result.get('error')}"
        assert result["format"] == "png"
        internal_path = user_context.exported_files[-1]["internal_path"]
        png_path = resolve_attachment_internal_path(
            internal_path, "e2e_user", "e2e_thread"
        )
        assert png_path.exists(), "PNG 应落盘"
        assert png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "应为合法 PNG"

    @pytest.mark.asyncio
    async def test_e2e_export_document_docx(self, tool_runtime_base_url, user_context):
        """ExportDocument → 真 /convert/pandoc: Markdown → 合法 DOCX (zip).

        独特价值: 验证真实 pandoc 转换 + 我们的 _run_pandoc HTTP 调用契约.
        """
        from src.core.path_resolver import resolve_attachment_internal_path
        from src.tools.external.export_document import service as ed_service

        with (
            _patch_register_deps(),
            patch.object(ed_service, "schedule_summary_generation"),
        ):
            result = await ed_service.run_export_document(
                content="# 你好世界\n\n这是一段测试文本。",
                style="default",
                output_format="docx",
                filename="e2e_doc",
                summary=None,
                user_id="e2e_user",
                thread_id="e2e_thread",
            )

        assert result["success"] is True, f"转换失败: {result.get('error')}"
        assert result["format"] == "docx"
        internal_path = user_context.exported_files[-1]["internal_path"]
        docx_path = resolve_attachment_internal_path(
            internal_path, "e2e_user", "e2e_thread"
        )
        assert docx_path.exists(), "DOCX 应落盘"
        assert docx_path.read_bytes()[:2] == b"PK", "应为合法 DOCX (zip)"

    @pytest.mark.asyncio
    async def test_e2e_export_document_pdf(self, tool_runtime_base_url, user_context):
        """ExportDocument → 真 /convert/pandoc + /render/pdf: Markdown → 合法 PDF.

        独特价值: 验证真实 pandoc(md→html) + Chromium(html→pdf) 双步 + 我们的 HTTP 契约.
        """
        from src.core.path_resolver import resolve_attachment_internal_path
        from src.tools.external.export_document import service as ed_service

        with (
            _patch_register_deps(),
            patch.object(ed_service, "schedule_summary_generation"),
        ):
            result = await ed_service.run_export_document(
                content="# PDF 测试\n\n这是一段 PDF 测试文本。",
                style="default",
                output_format="pdf",
                filename="e2e_pdf",
                summary=None,
                user_id="e2e_user",
                thread_id="e2e_thread",
            )

        assert result["success"] is True, f"渲染失败: {result.get('error')}"
        assert result["format"] == "pdf"
        internal_path = user_context.exported_files[-1]["internal_path"]
        pdf_path = resolve_attachment_internal_path(
            internal_path, "e2e_user", "e2e_thread"
        )
        assert pdf_path.exists(), "PDF 应落盘"
        assert pdf_path.read_bytes()[:4] == b"%PDF", "应为合法 PDF"
