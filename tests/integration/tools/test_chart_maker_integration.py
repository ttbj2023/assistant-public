"""ChartMaker 外部工具编排集成测试.

验证 chart_maker 服务层的渲染编排和 exported_files 反馈链路, 镜像
export_document 集成测试的既定模式. 单元测试 (test_chart_maker) mock 在
get_browser_renderer / run_chart_maker 边界, 未覆盖 register_tool_output 文件
注册 + exported_files ContextVar 反馈的跨组件协作.

测试策略: 灰盒 - Mock 外部渲染 (Playwright via get_browser_renderer) 和 DB 依赖
(去重/配额/签名URL配置), 保留真实编排逻辑 (路径解析/文件写入/UserContext 透传).
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
class TestChartMakerOrchestrationIntegration:
    """ChartMaker 渲染编排和 exported_files 反馈集成测试."""

    @pytest.mark.asyncio
    async def test_chart_pipeline_exported_files_feedback(self):
        """图表渲染管线 + exported_files ContextVar 反馈.

        协作场景: run_chart_maker → get_browser_renderer.render_chart →
            register_tool_output → ctx.exported_files
        设计思路: 用真实 set_user_context + 真实路径解析, Mock 渲染器(写假PNG)和 DB 依赖,
                  验证完整编排链和 exported_files 回填
        业务价值: 确保图表产出后对话历史能获取文件URL(exported_files 是 chat.py 替换标记的数据源)
        """
        from src.tools.external.chart_maker import service as cm_service

        ctx = UserContext(
            user_id="testuser",
            thread_id="main",
            agent_id="test-agent",
            round_number=1,
        )
        token = set_user_context(ctx)

        async def fake_render_chart(
            *,
            engine,
            code,
            title,
            width,
            height,
            scale,
            output_path,
        ):
            output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mock_renderer = MagicMock()
        mock_renderer.render_chart = fake_render_chart

        try:
            with (
                _patch_register_deps(),
                patch.object(
                    cm_service,
                    "get_browser_renderer",
                    return_value=mock_renderer,
                ),
            ):
                result = await cm_service.run_chart_maker(
                    engine="mermaid",
                    code="graph TD; A-->B;",
                    filename="test_chart",
                    title="测试图表",
                    user_id="testuser",
                    thread_id="main",
                )

            assert result["success"] is True
            assert result["format"] == "png"
            assert result["size_bytes"] > 0
            assert result["engine"] == "mermaid"

            exported = get_user_context().exported_files
            assert len(exported) == 1, "exported_files 应包含 1 个文件记录"
            entry = exported[0]
            assert entry["format"] == "png"
            assert entry["file_type"] == "image"
            assert "test_chart" in entry["filename"]
            assert entry["url"].startswith("http://localhost:8000/v1/files/dl/")
            assert entry["internal_path"].startswith("files/exports/charts/")
        finally:
            reset_user_context(token)

    @pytest.mark.asyncio
    async def test_chart_render_failure_returns_structured_error(self):
        """渲染器抛错时返回结构化错误 (不向调用方传播异常).

        协作场景: run_chart_maker 捕获 get_browser_renderer 渲染异常, 返回结构化错误 dict
        设计思路: Mock 渲染器抛异常, 验证 run_chart_maker 不抛出且返回 success=False
        业务价值: 渲染失败不得崩 Agent 循环, 须返回可读错误
        """
        from src.tools.external.chart_maker import service as cm_service

        ctx = UserContext(
            user_id="testuser",
            thread_id="main",
            agent_id="test-agent",
            round_number=1,
        )
        token = set_user_context(ctx)

        mock_renderer = MagicMock()

        async def failing_render(**kwargs):
            raise RuntimeError("Playwright 渲染超时")

        mock_renderer.render_chart = failing_render

        try:
            with (
                _patch_register_deps(),
                patch.object(
                    cm_service,
                    "get_browser_renderer",
                    return_value=mock_renderer,
                ),
            ):
                result = await cm_service.run_chart_maker(
                    engine="vega_lite",
                    code='{"invalid"',
                    filename="bad_chart",
                    title=None,
                    user_id="testuser",
                    thread_id="main",
                )

            assert result["success"] is False
            assert "渲染" in result["error"] or "Playwright" in result["error"]
        finally:
            reset_user_context(token)
