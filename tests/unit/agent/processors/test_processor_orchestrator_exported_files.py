"""ProcessorOrchestrator导出文件附件处理测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.agent.processors.processor_orchestrator import ProcessorOrchestrator
from src.core.context import UserContext, reset_user_context, set_user_context


def _mock_conv_service(round_num: int = 1, latest: int = 0) -> Mock:
    """创建mock对话服务."""
    svc = Mock()
    svc.allocate_round_number = AsyncMock(return_value=round_num)
    svc.get_latest_round_number = AsyncMock(return_value=latest)
    return svc


@pytest.fixture
def orchestrator() -> ProcessorOrchestrator:
    """创建已初始化的协调器."""
    with (
        patch("src.agent.processors.processor_orchestrator.LocalMemoryProcessor"),
        patch("src.agent.processors.processor_orchestrator.InferenceCoordinator"),
    ):
        return ProcessorOrchestrator({"model": {"llm": "test-model"}}, "local")


@pytest.mark.asyncio
async def test_build_with_exported_image_file(
    orchestrator: ProcessorOrchestrator,
) -> None:
    """图片生成导出文件应以image类型合并附件."""
    ctx_token = set_user_context(
        UserContext(
            user_id="u1",
            thread_id="t1",
            agent_id="a1",
            exported_files=[
                {
                    "url": "http://127.0.0.1:8000/v1/files/dl/token/cat.png",
                    "file_id": "abc12345",
                    "file_type": "image",
                    "brief": "生成图片",
                    "internal_path": "files/images/generated/cat.png",
                    "filename": "cat.png",
                    "detail": "生成提示词: cat",
                    "size_bytes": 8,
                    "format": "png",
                }
            ],
        )
    )
    try:
        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            return_value=_mock_conv_service(5, 3),
        ):
            data = await orchestrator._build_conversation_data(
                user_input="Hello",
                response_content="已生成 http://127.0.0.1:8000/v1/files/dl/token/cat.png",
                user_id="u1",
                thread_id="t1",
                agent_id="a1",
            )

        assert "[file: abc12345] 生成图片" in data.assistant_response
    finally:
        reset_user_context(ctx_token)


@pytest.mark.asyncio
async def test_build_appends_attachment_markers_when_no_url_in_response(
    orchestrator: ProcessorOrchestrator,
) -> None:
    """LLM 原始输出不含 URL 时, 仍应在 assistant_response 末尾追加附件标记."""
    ctx_token = set_user_context(
        UserContext(
            user_id="u1",
            thread_id="t1",
            agent_id="a1",
            exported_files=[
                {
                    "url": "http://127.0.0.1:8000/v1/files/dl/token/report.pdf",
                    "file_id": "358dc44d",
                    "file_type": "document",
                    "brief": "PDF导出: report.pdf (12.0KB)",
                    "internal_path": "files/documents/report_20260101_120000_a1b2c3d4.pdf",
                    "filename": "report_20260101_120000_a1b2c3d4.pdf",
                    "detail": "Markdown 转 PDF 导出",
                    "size_bytes": 12288,
                    "format": "pdf",
                }
            ],
        )
    )
    try:
        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            return_value=_mock_conv_service(19, 17),
        ):
            data = await orchestrator._build_conversation_data(
                user_input="重新生成下载链接",
                response_content="已导出 👇",
                user_id="u1",
                thread_id="t1",
                agent_id="a1",
            )

        assert (
            "[file: 358dc44d] PDF导出: report.pdf (12.0KB)" in data.assistant_response
        )
        assert "http://127.0.0.1:8000" not in data.assistant_response
        assert data.assistant_response.startswith("已导出 👇")
    finally:
        reset_user_context(ctx_token)


@pytest.mark.asyncio
async def test_build_does_not_duplicate_existing_attachment_markers(
    orchestrator: ProcessorOrchestrator,
) -> None:
    """LLM 原始输出已含附件标记时, 不应重复追加."""
    ctx_token = set_user_context(
        UserContext(
            user_id="u1",
            thread_id="t1",
            agent_id="a1",
            exported_files=[
                {
                    "url": "http://127.0.0.1:8000/v1/files/dl/token/report.pdf",
                    "file_id": "358dc44d",
                    "file_type": "document",
                    "brief": "PDF导出: report.pdf (12.0KB)",
                    "internal_path": "files/documents/report.pdf",
                    "filename": "report.pdf",
                    "detail": "Markdown 转 PDF 导出",
                    "size_bytes": 12288,
                    "format": "pdf",
                }
            ],
        )
    )
    try:
        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            return_value=_mock_conv_service(19, 17),
        ):
            data = await orchestrator._build_conversation_data(
                user_input="导出 PDF",
                response_content="文件已生成: [file: 358dc44d] report.pdf",
                user_id="u1",
                thread_id="t1",
                agent_id="a1",
            )

        assert data.assistant_response == "文件已生成: [file: 358dc44d] report.pdf"
    finally:
        reset_user_context(ctx_token)


@pytest.mark.asyncio
async def test_build_appends_multiple_attachment_markers(
    orchestrator: ProcessorOrchestrator,
) -> None:
    """多个 exported_files 时应逐条追加附件标记."""
    ctx_token = set_user_context(
        UserContext(
            user_id="u1",
            thread_id="t1",
            agent_id="a1",
            exported_files=[
                {
                    "url": "http://127.0.0.1:8000/v1/files/dl/token/a.png",
                    "file_id": "aaaaaaaa",
                    "file_type": "image",
                    "brief": "图 A",
                    "internal_path": "files/images/a.png",
                    "filename": "a.png",
                    "detail": "图 A",
                    "size_bytes": 1024,
                    "format": "png",
                },
                {
                    "url": "http://127.0.0.1:8000/v1/files/dl/token/b.pdf",
                    "file_id": "bbbbbbbb",
                    "file_type": "document",
                    "brief": "PDF B",
                    "internal_path": "files/documents/b.pdf",
                    "filename": "b.pdf",
                    "detail": "PDF B",
                    "size_bytes": 2048,
                    "format": "pdf",
                },
            ],
        )
    )
    try:
        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            return_value=_mock_conv_service(3, 1),
        ):
            data = await orchestrator._build_conversation_data(
                user_input="导出多个文件",
                response_content="已生成",
                user_id="u1",
                thread_id="t1",
                agent_id="a1",
            )

        assert "[file: aaaaaaaa] 图 A" in data.assistant_response
        assert "[file: bbbbbbbb] PDF B" in data.assistant_response
        assert data.assistant_response.count("[file:") == 2
    finally:
        reset_user_context(ctx_token)


@pytest.mark.asyncio
async def test_build_without_exported_files_keeps_response_unchanged(
    orchestrator: ProcessorOrchestrator,
) -> None:
    """无 exported_files 时 assistant_response 保持原样."""
    ctx_token = set_user_context(
        UserContext(
            user_id="u1",
            thread_id="t1",
            agent_id="a1",
        )
    )
    try:
        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            return_value=_mock_conv_service(2, 1),
        ):
            data = await orchestrator._build_conversation_data(
                user_input="普通对话",
                response_content="普通回复",
                user_id="u1",
                thread_id="t1",
                agent_id="a1",
            )

        assert data.assistant_response == "普通回复"
    finally:
        reset_user_context(ctx_token)
