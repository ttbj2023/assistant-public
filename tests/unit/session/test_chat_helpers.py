"""chat_helpers 单元测试.

覆盖 prepare_image_attachments 的多模态/非多模态分支行为,
确保 is_multimodal 参数正确决定同步/异步描述生成策略.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.files.models import AttachmentDTO
from src.session.chat_helpers import prepare_image_attachments
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
