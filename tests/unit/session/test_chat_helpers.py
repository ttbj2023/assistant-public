"""chat_helpers 单元测试.

覆盖 prepare_image_attachments 的多模态/非多模态分支行为,
确保 is_multimodal 参数正确决定同步/异步描述生成策略.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.files.models import AttachmentDTO
from src.session.chat_helpers import (
    prepare_binary_document_attachments,
    prepare_document_attachments,
    prepare_image_attachments,
)
from src.utils.message_formatting import format_user_message_with_attachments


@pytest.fixture
def image_datas():
    """标准测试图片数据."""
    return [
        {"data": b"fake-image-bytes", "mime_type": "image/jpeg"},
    ]


@pytest.fixture
def attachment_info():
    """模拟保存后的附件信息."""
    info = MagicMock()
    info.id = "abc123"
    info.url = "files/images/round_1_img.jpg"
    return info


class TestPrepareImageAttachments:
    """prepare_image_attachments 行为测试."""

    @pytest.mark.asyncio
    async def test_multimodal_skips_describer_and_spawns_background_task(
        self,
        image_datas,
        attachment_info,
    ):
        """多模态模型: store_image image_describer=None, 并触发后台任务."""
        with (
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
            patch("src.session.chat_helpers.ImageDescriber") as mock_describer_cls,
            patch(
                "src.session.chat_helpers.get_user_path_resolver"
            ) as mock_resolver_fn,
            patch("src.session.chat_helpers.spawn_background_task") as mock_spawn,
        ):
            repository = MagicMock()
            repository.store_image = AsyncMock(return_value=attachment_info)
            mock_repo_fn.return_value = repository

            resolver = MagicMock()
            resolver.get_shared_storage_path.return_value = MagicMock()
            mock_resolver_fn.return_value = resolver

            result = await prepare_image_attachments(
                user_id="u1",
                thread_id="t1",
                is_multimodal=True,
                image_datas=image_datas,
                round_number=1,
            )

        assert len(result) == 1
        repository.store_image.assert_awaited_once()
        call_kwargs = repository.store_image.call_args.kwargs
        assert call_kwargs["image_describer"] is None
        mock_spawn.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_multimodal_passes_describer_and_no_spawn(
        self,
        image_datas,
        attachment_info,
    ):
        """非多模态模型: store_image 传入 ImageDescriber, 不触发后台任务."""
        with (
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
            patch("src.session.chat_helpers.ImageDescriber") as mock_describer_cls,
            patch("src.session.chat_helpers.spawn_background_task") as mock_spawn,
        ):
            repository = MagicMock()
            repository.store_image = AsyncMock(return_value=attachment_info)
            mock_repo_fn.return_value = repository

            result = await prepare_image_attachments(
                user_id="u1",
                thread_id="t1",
                is_multimodal=False,
                image_datas=image_datas,
                round_number=1,
            )

        assert len(result) == 1
        repository.store_image.assert_awaited_once()
        call_kwargs = repository.store_image.call_args.kwargs
        assert call_kwargs["image_describer"] is not None
        mock_spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_image_datas_returns_empty_list(self):
        """空图片列表直接返回空列表, 不初始化仓库."""
        with patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn:
            result = await prepare_image_attachments(
                user_id="u1",
                thread_id="t1",
                is_multimodal=True,
                image_datas=[],
                round_number=1,
            )

        assert result == []
        mock_repo_fn.assert_not_called()


# ============================================================================
# prepare_document_attachments (纯文本文档)
# ============================================================================


class TestPrepareDocumentAttachments:
    """prepare_document_attachments 行为测试."""

    @pytest.mark.asyncio
    async def test_empty_documents_returns_empty_list(self):
        """空文档列表直接返回空列表, 不初始化仓库."""
        with patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn:
            result = await prepare_document_attachments(
                user_id="u1",
                thread_id="t1",
                document_datas=[],
                round_number=1,
            )

        assert result == []
        mock_repo_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_documents_stored_and_dtos_returned_in_order(self):
        """多文档逐个 store_document, 顺序返回 DTO 列表."""
        # spec=AttachmentDTO 会挡住 pydantic 字段访问 (注解非类属性), 用普通 Mock
        dto1 = MagicMock(internal_path="files/documents/a.md")
        dto2 = MagicMock(internal_path="files/documents/b.txt")

        with patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn:
            repository = MagicMock()
            repository.store_document = AsyncMock(side_effect=[dto1, dto2])
            mock_repo_fn.return_value = repository

            result = await prepare_document_attachments(
                user_id="u1",
                thread_id="t1",
                document_datas=[
                    {"filename": "a.md", "content": "A"},
                    {"filename": "b.txt", "content": "B"},
                ],
                round_number=3,
            )

        assert result == [dto1, dto2]
        assert repository.store_document.await_count == 2
        first_call = repository.store_document.await_args_list[0]
        assert first_call.kwargs["filename"] == "a.md"
        assert first_call.kwargs["content"] == "A"
        assert first_call.kwargs["round_number"] == 3

    @pytest.mark.asyncio
    async def test_store_failure_skips_and_continues(self):
        """单个文档保存失败不中断其余文档."""
        dto2 = MagicMock(internal_path="files/documents/b.txt")

        with patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn:
            repository = MagicMock()
            repository.store_document = AsyncMock(
                side_effect=[OSError("磁盘错误"), dto2]
            )
            mock_repo_fn.return_value = repository

            result = await prepare_document_attachments(
                user_id="u1",
                thread_id="t1",
                document_datas=[
                    {"filename": "a.md", "content": "A"},
                    {"filename": "b.txt", "content": "B"},
                ],
                round_number=1,
            )

        assert result == [dto2]

    @pytest.mark.asyncio
    async def test_code_and_plain_documents_trigger_respective_summaries(self):
        """代码文件触发代码摘要, 普通文档触发文档概要后台任务."""
        dto_py = MagicMock(file_id="py000001", internal_path="files/documents/a.py")
        dto_md = MagicMock(file_id="md000002", internal_path="files/documents/b.md")

        with (
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
            patch("src.session.chat_helpers.spawn_background_task") as mock_spawn,
        ):
            repository = MagicMock()
            repository.store_document = AsyncMock(side_effect=[dto_py, dto_md])
            mock_repo_fn.return_value = repository

            await prepare_document_attachments(
                user_id="u1",
                thread_id="t1",
                document_datas=[
                    {"filename": "a.py", "content": "print(1)"},
                    {"filename": "b.md", "content": "B"},
                ],
                round_number=1,
            )

        # 代码文件 -> 代码摘要; 普通文档 -> 文档概要
        spawned_names = {c.args[0].__name__ for c in mock_spawn.call_args_list}
        assert spawned_names == {
            "background_generate_code_summary",
            "background_generate_document_summary",
        }


# ============================================================================
# background_generate_code_summary (代码文件后台摘要)
# ============================================================================


class TestBackgroundGenerateCodeSummary:
    """background_generate_code_summary 行为测试."""

    @pytest.mark.asyncio
    async def test_updates_description_with_composed_desc(self):
        """摘要完成后调 update_description, brief 进 DB, desc=摘要+原文."""
        from src.session.chat_helpers import background_generate_code_summary

        with (
            patch(
                "src.inference.code_description.describer.CodeDescriber.describe",
                new=AsyncMock(return_value=("Python 入口脚本", "语言: Python")),
            ) as mock_describe,
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
        ):
            repository = MagicMock()
            repository.update_description = AsyncMock()
            mock_repo_fn.return_value = repository

            await background_generate_code_summary(
                file_id="py000001",
                filename="a.py",
                content="print(1)",
            )

        mock_describe.assert_awaited_once()
        repository.update_description.assert_awaited_once()
        call_args = repository.update_description.call_args
        assert call_args.args[0] == "py000001"
        # DB brief = 一句话; desc 摘要区 = 结构化摘要, 原文区 = 代码
        assert call_args.args[1] == "Python 入口脚本"
        assert call_args.kwargs["desc_summary"] == "语言: Python"
        assert call_args.args[2] == "print(1)"

    @pytest.mark.asyncio
    async def test_describe_failure_swallows_and_no_update(self):
        """描述失败不抛异常且不更新注册表."""
        from src.session.chat_helpers import background_generate_code_summary

        with (
            patch(
                "src.inference.code_description.describer.CodeDescriber.describe",
                new=AsyncMock(return_value=("", "")),
            ),
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
        ):
            repository = MagicMock()
            repository.update_description = AsyncMock()
            mock_repo_fn.return_value = repository

            # 不应抛异常
            await background_generate_code_summary(
                file_id="py000001",
                filename="a.py",
                content="print(1)",
            )

        repository.update_description.assert_not_awaited()


# ============================================================================
# background_generate_document_summary (普通文档后台概要)
# ============================================================================


class TestBackgroundGenerateDocumentSummary:
    """background_generate_document_summary 行为测试."""

    @pytest.mark.asyncio
    async def test_updates_description_with_composed_desc(self):
        """概要完成后调 update_description, brief 进 DB, desc=概要+原文."""
        from src.session.chat_helpers import background_generate_document_summary

        with (
            patch(
                "src.inference.document_description.describer.DocDescriber.describe",
                new=AsyncMock(return_value=("季度财务报告", "2026年Q2营收增长15%")),
            ) as mock_describe,
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
        ):
            repository = MagicMock()
            repository.update_description = AsyncMock()
            mock_repo_fn.return_value = repository

            await background_generate_document_summary(
                file_id="md000002",
                filename="b.md",
                content="报告全文",
            )

        mock_describe.assert_awaited_once()
        repository.update_description.assert_awaited_once()
        call_args = repository.update_description.call_args
        assert call_args.args[0] == "md000002"
        assert call_args.args[1] == "季度财务报告"
        assert call_args.kwargs["desc_summary"] == "2026年Q2营收增长15%"
        assert call_args.args[2] == "报告全文"

    @pytest.mark.asyncio
    async def test_describe_failure_swallows_and_no_update(self):
        """概要失败不抛异常且不更新注册表."""
        from src.session.chat_helpers import background_generate_document_summary

        with (
            patch(
                "src.inference.document_description.describer.DocDescriber.describe",
                new=AsyncMock(return_value=("", "")),
            ),
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
        ):
            repository = MagicMock()
            repository.update_description = AsyncMock()
            mock_repo_fn.return_value = repository

            await background_generate_document_summary(
                file_id="md000002",
                filename="b.md",
                content="报告全文",
            )

        repository.update_description.assert_not_awaited()


# ============================================================================
# prepare_binary_document_attachments (二进制文档编排)
# ============================================================================


def _binary_doc(filename: str, data: bytes = b"\x50\x4b fake") -> dict:
    import base64

    return {"filename": filename, "content_b64": base64.b64encode(data).decode()}


def _doc2md_result(**kwargs):
    from src.files.doc2md_client import Doc2mdResult

    defaults = {
        "markdown": "# 转换结果",
        "mode": "digital-pdf",
        "pages": 3,
        "elapsed": 0.5,
        "images": [],
    }
    defaults.update(kwargs)
    return Doc2mdResult(**defaults)


class TestPrepareBinaryDocumentAttachments:
    """prepare_binary_document_attachments 行为测试."""

    @pytest.mark.asyncio
    async def test_digital_document_converts_stores_and_spawns_summary(self):
        """数字版: 同步转换 + 内嵌图替换 + 存原件 + spawn AI 概要."""
        dto = MagicMock(file_id="bd000001", file_type="document")

        with (
            patch("src.session.chat_helpers.get_doc2md_client") as mock_client_fn,
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
            patch("src.session.chat_helpers.spawn_background_task") as mock_spawn,
        ):
            client = MagicMock()
            client.is_configured = MagicMock(return_value=True)
            client.convert = AsyncMock(return_value=_doc2md_result())
            mock_client_fn.return_value = client

            repository = MagicMock()
            repository.store_binary_document = AsyncMock(return_value=dto)
            repository.resolve_embedded_images = AsyncMock(
                return_value=("# 转换结果 [图](file:img1)", ["img1"])
            )
            mock_repo_fn.return_value = repository

            result = await prepare_binary_document_attachments(
                user_id="u1",
                thread_id="t1",
                binary_docs=[_binary_doc("报告.pdf")],
                round_number=1,
            )

        assert result == [dto]
        client.convert.assert_awaited_once_with(
            "报告.pdf", b"\x50\x4b fake", defer_scan=True
        )
        repository.store_binary_document.assert_awaited_once()
        store_kwargs = repository.store_binary_document.call_args.kwargs
        assert store_kwargs["markdown"] == "# 转换结果 [图](file:img1)"
        assert store_kwargs["mode"] == "digital-pdf"

        # spawn AI 概要 (复用 Phase 0 的文档概要任务)
        spawned = [c.args[0].__name__ for c in mock_spawn.call_args_list]
        assert "background_generate_document_summary" in spawned

    @pytest.mark.asyncio
    async def test_scan_deferred_stores_and_spawns_background_convert(self):
        """扫描件: 延迟存储 (brief 含解析中) + spawn 后台转换."""
        dto = MagicMock(file_id="bd000002", file_type="document")

        with (
            patch("src.session.chat_helpers.get_doc2md_client") as mock_client_fn,
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
            patch("src.session.chat_helpers.spawn_background_task") as mock_spawn,
        ):
            client = MagicMock()
            client.is_configured = MagicMock(return_value=True)
            client.convert = AsyncMock(
                return_value=_doc2md_result(mode="scan-deferred", pages=12, markdown="")
            )
            mock_client_fn.return_value = client

            repository = MagicMock()
            repository.store_binary_document = AsyncMock(return_value=dto)
            mock_repo_fn.return_value = repository

            result = await prepare_binary_document_attachments(
                user_id="u1",
                thread_id="t1",
                binary_docs=[_binary_doc("扫描件.pdf")],
                round_number=1,
            )

        assert result == [dto]
        store_kwargs = repository.store_binary_document.call_args.kwargs
        assert store_kwargs["markdown"] == ""
        assert store_kwargs["pages"] == 12

        spawned = [c.args[0].__name__ for c in mock_spawn.call_args_list]
        assert "background_convert_scanned_document" in spawned

    @pytest.mark.asyncio
    async def test_scan_over_page_limit_stores_original_only(self):
        """超页数上限: 只存原件, 不启动后台转换."""
        dto = MagicMock(file_id="bd000003")

        with (
            patch("src.session.chat_helpers.get_doc2md_client") as mock_client_fn,
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
            patch("src.session.chat_helpers.spawn_background_task") as mock_spawn,
        ):
            client = MagicMock()
            client.is_configured = MagicMock(return_value=True)
            client.convert = AsyncMock(
                return_value=_doc2md_result(
                    mode="scan-deferred", pages=120, markdown=""
                )
            )
            mock_client_fn.return_value = client

            repository = MagicMock()
            repository.store_binary_document = AsyncMock(return_value=dto)
            mock_repo_fn.return_value = repository

            await prepare_binary_document_attachments(
                user_id="u1",
                thread_id="t1",
                binary_docs=[_binary_doc("大扫描件.pdf")],
                round_number=1,
            )

        mock_spawn.assert_not_called()
        store_kwargs = repository.store_binary_document.call_args.kwargs
        assert "超出解析上限" in store_kwargs["brief"]

    @pytest.mark.asyncio
    async def test_service_unconfigured_degrades_to_original_only(self):
        """服务未配置: 降级存原件 (brief 说明), 不调 convert."""
        dto = MagicMock(file_id="bd000004")

        with (
            patch("src.session.chat_helpers.get_doc2md_client") as mock_client_fn,
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
            patch("src.session.chat_helpers.spawn_background_task") as mock_spawn,
        ):
            client = MagicMock()
            client.is_configured = MagicMock(return_value=False)
            client.convert = AsyncMock()
            mock_client_fn.return_value = client

            repository = MagicMock()
            repository.store_binary_document = AsyncMock(return_value=dto)
            mock_repo_fn.return_value = repository

            result = await prepare_binary_document_attachments(
                user_id="u1",
                thread_id="t1",
                binary_docs=[_binary_doc("报告.docx")],
                round_number=1,
            )

        assert result == [dto]
        client.convert.assert_not_awaited()
        mock_spawn.assert_not_called()
        store_kwargs = repository.store_binary_document.call_args.kwargs
        assert "仅保存原件" in store_kwargs["brief"]

    @pytest.mark.asyncio
    async def test_service_error_degrades_and_continues(self):
        """服务调用失败: 该文档降级存原件, 不中断其他文档."""
        from src.files.doc2md_client import Doc2mdError

        dto = MagicMock(file_id="bd000005")

        with (
            patch("src.session.chat_helpers.get_doc2md_client") as mock_client_fn,
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
            patch("src.session.chat_helpers.spawn_background_task"),
        ):
            client = MagicMock()
            client.is_configured = MagicMock(return_value=True)
            client.convert = AsyncMock(side_effect=Doc2mdError("服务不可达"))
            mock_client_fn.return_value = client

            repository = MagicMock()
            repository.store_binary_document = AsyncMock(return_value=dto)
            mock_repo_fn.return_value = repository

            result = await prepare_binary_document_attachments(
                user_id="u1",
                thread_id="t1",
                binary_docs=[_binary_doc("a.pdf")],
                round_number=1,
            )

        assert result == [dto]
        store_kwargs = repository.store_binary_document.call_args.kwargs
        assert "仅保存原件" in store_kwargs["brief"]


class TestBackgroundConvertScannedDocument:
    """background_convert_scanned_document 行为测试."""

    @pytest.mark.asyncio
    async def test_full_pipeline_updates_desc_and_pushes(self):
        """OCR 完成: 图替换 → 概要 → update_description → push 通知."""
        from src.session.chat_helpers import background_convert_scanned_document

        with (
            patch("src.session.chat_helpers.get_doc2md_client") as mock_client_fn,
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
            patch(
                "src.inference.document_description.describer.DocDescriber.describe",
                new=AsyncMock(return_value=("扫描件概要", "详细概要内容")),
            ) as mock_describe,
            patch("src.core.notification.resolve_delivery") as mock_resolve,
            patch("src.core.notification.get_notification_service") as mock_notif_fn,
        ):
            client = MagicMock()
            client.convert = AsyncMock(return_value=_doc2md_result(mode="ocr", pages=5))
            mock_client_fn.return_value = client

            repository = MagicMock()
            repository.resolve_embedded_images = AsyncMock(
                return_value=("# OCR 结果", ["img1"])
            )
            repository.update_description = AsyncMock()
            mock_repo_fn.return_value = repository

            delivery = MagicMock()
            mock_resolve.return_value = delivery
            notification = MagicMock()
            notification.send = AsyncMock(return_value=True)
            mock_notif_fn.return_value = notification

            await background_convert_scanned_document(
                file_id="bd000006",
                filename="扫描件.pdf",
                data=b"pdf-bytes",
                user_id="u1",
                thread_id="t1",
                agent_id="a1",
            )

        # 全量转换 (defer_scan=False)
        client.convert.assert_awaited_once_with("扫描件.pdf", b"pdf-bytes")
        # 概要 → desc 更新
        mock_describe.assert_awaited_once()
        repository.update_description.assert_awaited_once()
        call = repository.update_description.call_args
        assert call.args[0] == "bd000006"
        assert call.args[1] == "扫描件概要"
        assert call.args[2] == "# OCR 结果"
        # push 通知 (wechat 渠道)
        mock_resolve.assert_awaited_once_with("u1", "t1", "a1", "wechat")
        notification.send.assert_awaited_once()
        push_text = notification.send.call_args.args[1]
        assert "解析完成" in push_text
        assert "扫描件概要" in push_text

    @pytest.mark.asyncio
    async def test_convert_failure_updates_brief_and_no_push(self):
        """后台转换失败: brief 更新为失败说明, 不 push."""
        from src.files.doc2md_client import Doc2mdError
        from src.session.chat_helpers import background_convert_scanned_document

        with (
            patch("src.session.chat_helpers.get_doc2md_client") as mock_client_fn,
            patch("src.session.chat_helpers.get_file_repository") as mock_repo_fn,
            patch("src.core.notification.resolve_delivery") as mock_resolve,
        ):
            client = MagicMock()
            client.convert = AsyncMock(side_effect=Doc2mdError("OCR 崩溃"))
            mock_client_fn.return_value = client

            repository = MagicMock()
            repository.update_description = AsyncMock()
            mock_repo_fn.return_value = repository

            await background_convert_scanned_document(
                file_id="bd000007",
                filename="坏.pdf",
                data=b"x",
                user_id="u1",
                thread_id="t1",
                agent_id="a1",
            )

        repository.update_description.assert_awaited_once()
        brief = repository.update_description.call_args.args[1]
        assert "解析失败" in brief
        mock_resolve.assert_not_awaited()


# ============================================================================
# format_user_message_with_attachments (从 test_attachment_service.py 迁移)
# ============================================================================


class TestFormatUserMessageWithAttachments:
    """测试format_user_message_with_attachments - 消息格式化 (同步函数)."""

    def test_format_with_no_attachments_returns_original_text(self):
        """无附件时应返回原始文本."""
        result = format_user_message_with_attachments("你好", [])
        assert result == "你好"

    def test_format_with_image_attachment_appends_image_info(self):
        """图片附件应附加标记格式 (无id时回退到[img:])."""
        attachment = AttachmentDTO(
            file_id="",
            file_type="image",
            internal_path="files/images/round_1_test123.jpg",
            filename="round_1_test123.jpg",
            detail="一张测试图片",
            file_size=1024,
        )
        result = format_user_message_with_attachments("这是什么?", [attachment])

        assert "这是什么?" in result
        assert "[img:" in result
        assert "files/images/round_1_test123.jpg" in result

    def test_format_with_audio_attachment_appends_audio_info(self):
        """音频附件应附加[audio: url]格式."""
        attachment = AttachmentDTO(
            file_id="",
            file_type="audio",
            internal_path="files/audio/round_1_test123.mp3",
            filename="round_1_test123.mp3",
            file_size=2048,
        )
        result = format_user_message_with_attachments("播放这段音频", [attachment])

        assert "[audio:" in result
        assert "files/audio/round_1_test123.mp3" in result

    def test_format_with_video_attachment_appends_video_info(self):
        """视频附件应附加[video: url - description]格式."""
        attachment = AttachmentDTO(
            file_id="",
            file_type="video",
            internal_path="files/video/round_1_test123.mp4",
            filename="round_1_test123.mp4",
            brief="一个测试视频",
            file_size=4096,
        )
        result = format_user_message_with_attachments("分析视频", [attachment])

        assert "[video:" in result
        assert "一个测试视频" in result

    def test_format_with_multiple_attachments_combines_all(self):
        """多个附件应全部附加到文本中."""
        image = AttachmentDTO(
            file_id="",
            file_type="image",
            internal_path="files/images/round_1_test123.jpg",
            filename="round_1_test123.jpg",
            detail="图片",
            file_size=1024,
        )
        audio = AttachmentDTO(
            file_id="",
            file_type="audio",
            internal_path="files/audio/round_1_test123.mp3",
            filename="round_1_test123.mp3",
            file_size=2048,
        )
        result = format_user_message_with_attachments("看这个", [image, audio])

        assert "[img:" in result
        assert "[audio:" in result

    def test_format_with_empty_user_text_returns_attachments_only(self):
        """用户文本为空时应只返回附件信息."""
        attachment = AttachmentDTO(
            file_id="",
            file_type="image",
            internal_path="files/images/round_1_test123.jpg",
            filename="round_1_test123.jpg",
            detail="图片",
            file_size=1024,
        )
        result = format_user_message_with_attachments("", [attachment])

        assert "[img:" in result

    def test_format_with_image_no_description_uses_default(self):
        """图片附件无描述时应使用"图片"作为默认描述."""
        attachment = AttachmentDTO(
            file_id="",
            file_type="image",
            internal_path="files/images/test.jpg",
            filename="test.jpg",
            detail="",
            file_size=100,
        )
        result = format_user_message_with_attachments("看图", [attachment])

        assert "[img: files/images/test.jpg - 图片]" in result
