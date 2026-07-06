"""视频生成工具 - 将视频生成模型封装为主对话工具.

支持 Seedance 2.0 系列的全部输入模式:
- 文生视频: 仅 prompt
- 图生视频-首帧: prompt + images (含1张 first_frame)
- 图生视频-首尾帧: prompt + images (含1张 first_frame + 1张 last_frame)
- 多模态参考: prompt + images (reference_image) + videos + audios
"""

from __future__ import annotations

import base64
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, override

from pydantic import BaseModel, ConfigDict, Field

from src.core.path_resolver import (
    get_user_path_resolver,
    resolve_attachment_internal_path,
)
from src.files import AttachmentDTO
from src.inference.video_generation import (
    VideoContentBlock,
    VideoGenerationRequest,
    VideoGenerationService,
)
from src.tools.shared.base_internal_tool import BaseInternalTool
from src.tools.shared.query_alias_model import QueryAliasModel

logger = logging.getLogger(__name__)


class ImageInput(BaseModel):
    """图片输入引用."""

    source: str = Field(
        description="图片来源: attachment_id(附件ID) 或 URL",
    )
    role: Literal["first_frame", "last_frame", "reference_image"] = Field(
        default="reference_image",
        description="图片角色: first_frame(首帧), last_frame(尾帧), reference_image(参考图, 默认)",
    )


class VideoGenerationInput(QueryAliasModel):
    """视频生成输入."""

    _field_aliases: ClassVar[dict[str, str]] = {"query": "prompt"}

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    prompt: str = Field(
        min_length=1,
        max_length=2000,
        description="视频生成提示词, 描述期望生成的视频内容, 中文不超过500字, 英文不超过1000词",
    )
    duration: int = Field(
        default=5,
        ge=4,
        le=15,
        description="视频时长(秒), 范围4-15, 默认5",
    )
    ratio: Literal[
        "16:9",
        "4:3",
        "1:1",
        "3:4",
        "9:16",
        "21:9",
        "adaptive",
    ] = Field(default="adaptive", description="视频宽高比, 默认adaptive自动选择")
    resolution: Literal["480p", "720p", "1080p"] = Field(
        default="720p",
        description="视频分辨率, 默认720p",
    )
    images: list[ImageInput] | None = Field(
        default=None,
        description="参考图片列表, 每项含source(附件ID或URL)和role(first_frame/last_frame/reference_image). "
        "图生视频-首帧: 1张图片; 首尾帧: 2张; 多模态参考: 1-9张(reference_image)",
    )
    reference_videos: list[str] | None = Field(
        default=None,
        description="参考视频URL列表, 最多3个, 仅Seedance 2.0支持",
    )
    reference_audios: list[str] | None = Field(
        default=None,
        description="参考音频URL列表, 最多3个, 仅Seedance 2.0支持. "
        "不可单独传入音频, 需至少包含1张图片或1个视频",
    )
    filename: str | None = Field(
        default=None,
        max_length=120,
        description="输出文件名(不含扩展名), 可选",
    )
    brief: str | None = Field(
        default=None,
        max_length=120,
        description="视频一句话概要, 用于对话历史标记, 可选",
    )
    generate_audio: bool | None = Field(
        default=None,
        description="是否生成有声视频, 默认true",
    )
    seed: int | None = Field(
        default=None,
        ge=0,
        description="随机种子, 用于尽量复现生成结果, 可选",
    )
    watermark: bool | None = Field(default=None, description="是否添加水印, 可选")


class VideoGenerationTool(BaseInternalTool):
    """视频生成工具."""

    name: str = "generate_video"
    summary: str = "AI视频生成, 根据文字描述或参考图片/视频生成有声视频, 返回下载链接"
    search_keywords: ClassVar[list[str]] = [
        "视频",
        "生成视频",
        "拍视频",
        "做视频",
        "文生视频",
        "图生视频",
    ]
    description: str = (
        "AI视频生成工具, 根据文字描述或参考图片/视频生成有声视频并返回下载链接.\n"
        "支持模式: 文生视频(仅文字描述), 图生视频(图片作为首帧/尾帧), "
        "多模态参考(图片+视频+音频混合输入).\n"
        "默认720p 5秒 adaptive宽高比, 生成耗时1-3分钟.\n\n"
        "图片引用: 通过附件ID(如对话中的[file: a1b2c3d4])或URL指定.\n"
        '图生视频示例: {"prompt": "猫在窗边打哈欠", '
        '"images": [{"source": "a1b2c3d4", "role": "first_frame"}]}\n'
        '文生视频示例: {"prompt": "夕阳下的海边, 浪花拍打沙滩", "duration": 5}'
    )
    args_schema: type[VideoGenerationInput] = VideoGenerationInput

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        *,
        model_id: str = "ark-agent-plan:doubao-seedance-2.0",
        timeout: float = 600.0,
        agent_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(user_id, thread_id, agent_id=agent_id, **kwargs)
        object.__setattr__(self, "_model_id", model_id)
        object.__setattr__(self, "_timeout", timeout)
        object.__setattr__(self, "_service", VideoGenerationService())

    @override
    async def _arun(
        self,
        prompt: str,
        duration: int = 5,
        ratio: str = "adaptive",
        resolution: str = "720p",
        images: list[ImageInput] | None = None,
        reference_videos: list[str] | None = None,
        reference_audios: list[str] | None = None,
        filename: str | None = None,
        brief: str | None = None,
        generate_audio: bool | None = None,
        seed: int | None = None,
        watermark: bool | None = None,
    ) -> str:
        try:
            safe_filename = self._build_filename(filename)

            content_blocks = await self._build_content_blocks(prompt, images)

            self._add_video_blocks(content_blocks, reference_videos)
            self._add_audio_blocks(content_blocks, reference_audios)

            request = VideoGenerationRequest(
                content_blocks=content_blocks,
                ratio=ratio,
                duration=duration,
                resolution=resolution,
                generate_audio=generate_audio,
                seed=seed,
                watermark=watermark,
            )

            generated = await self._service.generate_video(
                model_id=self._model_id,
                request=request,
                timeout=self._timeout,
            )

            from src.files.paths import FILES_VIDEOS_GENERATED
            from src.tools.shared.file_output import register_tool_output

            video_dir = get_user_path_resolver().get_shared_storage_path(
                self.user_id,
                self.thread_id,
                FILES_VIDEOS_GENERATED,
            )
            output_path = video_dir / safe_filename
            output_path.write_bytes(generated.video_data)

            video_brief = brief or self._compose_brief(prompt, ratio, duration)
            input_summary = self._summarize_inputs(
                images, reference_videos, reference_audios
            )
            detail = self._compose_detail(
                prompt, ratio, resolution, duration, input_summary, generated.duration
            )

            reg_result = await register_tool_output(
                output_path=output_path,
                display_filename=safe_filename,
                output_filename=safe_filename,
                output_format="mp4",
                file_type="video",
                content=detail,
                summary=video_brief,
                user_id=self.user_id,
                thread_id=self.thread_id,
            )

            result_data = {
                "file_id": reg_result["file_id"],
                "filename": reg_result["filename"],
                "format": "mp4",
                "ratio": ratio,
                "resolution": resolution,
                "duration": duration,
                "size_bytes": reg_result["size_bytes"],
            }
            if generated.duration is not None:
                result_data["actual_duration"] = generated.duration

            return self._format_success(
                result_data,
                message=f"视频已生成: [file: {reg_result['file_id']}] {safe_filename}",
            )

        except Exception as e:
            logger.exception("VideoGenerationTool 执行失败: %s", e)
            return self._format_error(e)

    async def _build_content_blocks(
        self,
        prompt: str,
        images: list[ImageInput] | None,
    ) -> list[VideoContentBlock]:
        """构建内容块列表: 文本 + 图片."""
        blocks: list[VideoContentBlock] = [
            VideoContentBlock(type="text", text=prompt.strip()),
        ]

        if not images:
            return blocks

        for img in images:
            url = await self._resolve_image_source(img.source)
            blocks.append(
                VideoContentBlock(
                    type="image_url",
                    url=url,
                    role=img.role,
                )
            )

        return blocks

    async def _resolve_image_source(self, source: str) -> str:
        """解析图片来源: attachment_id → base64 data URL, URL → 原样返回."""
        if source.startswith(("http://", "https://", "data:")):
            return source

        entry = await self._get_attachment_entry(source)
        if not entry:
            raise ValueError(f"未找到附件: {source}")

        if entry.file_type != "image":
            raise ValueError(f"附件 {source} 不是图片类型(实际: {entry.file_type})")

        file_path = resolve_attachment_internal_path(
            entry.internal_path,
            self.user_id,
            self.thread_id,
        )
        if not file_path.exists():
            raise FileNotFoundError(f"图片文件已不存在: {entry.filename}")

        mime_type = self._guess_mime_type(entry, file_path)
        image_bytes = file_path.read_bytes()
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        return f"data:{mime_type};base64,{b64}"

    async def _get_attachment_entry(self, file_id: str) -> AttachmentDTO | None:
        """获取附件条目 (查 FileRegistry)."""
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        registry = await create_file_registry_service(self.user_id)
        db_entry = await registry.get(file_id)
        if not db_entry:
            return None

        internal_path = (
            db_entry.physical_path.split("shared/", 1)[-1]
            if "shared/" in db_entry.physical_path
            else db_entry.physical_path
        )
        return AttachmentDTO(
            file_id=db_entry.file_id,
            file_type=db_entry.file_type,
            internal_path=internal_path,
            filename=db_entry.filename,
            brief=db_entry.brief,
            detail="",
            file_format=db_entry.file_format,
            file_size=db_entry.file_size,
            content_hash=db_entry.content_hash,
            round_number=db_entry.round_number,
        )

    @staticmethod
    def _add_video_blocks(
        blocks: list[VideoContentBlock],
        videos: list[str] | None,
    ) -> None:
        """添加参考视频内容块."""
        if not videos:
            return
        for video_url in videos:
            blocks.append(
                VideoContentBlock(
                    type="video_url",
                    url=video_url,
                    role="reference_video",
                )
            )

    @staticmethod
    def _add_audio_blocks(
        blocks: list[VideoContentBlock],
        audios: list[str] | None,
    ) -> None:
        """添加参考音频内容块."""
        if not audios:
            return
        for audio_url in audios:
            blocks.append(
                VideoContentBlock(
                    type="audio_url",
                    url=audio_url,
                    role="reference_audio",
                )
            )

    @staticmethod
    def _guess_mime_type(entry: AttachmentDTO, image_path: Path) -> str:
        suffix = (entry.file_format or image_path.suffix.lstrip(".")).lower()
        mime_map = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
            "bmp": "image/bmp",
            "tiff": "image/tiff",
            "tif": "image/tiff",
            "heic": "image/heic",
            "heif": "image/heif",
        }
        return mime_map.get(suffix, "image/jpeg")

    @staticmethod
    def _build_filename(filename: str | None) -> str:
        if filename:
            name = filename.strip()
            if not name:
                raise ValueError("文件名不能为空")
            safe = re.sub(r"[^\w\-.]", "_", name)
            if safe != name:
                raise ValueError("文件名包含非法字符, 请使用字母/数字/下划线/连字符")
            stem = name.rsplit(".", 1)[0] if "." in name else name
        else:
            stem = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.urandom(4).hex()}"

        if len(stem) > 120:
            raise ValueError("文件名过长, 最大120字符")
        return f"{stem}.mp4"

    @staticmethod
    def _compose_brief(prompt: str, ratio: str, duration: int) -> str:
        normalized = " ".join(prompt.split())
        brief = normalized[:60]
        return f"生成视频 {ratio} {duration}s: {brief}"

    @staticmethod
    def _summarize_inputs(
        images: list[ImageInput] | None,
        videos: list[str] | None,
        audios: list[str] | None,
    ) -> str:
        parts: list[str] = []
        if images:
            roles = [img.role for img in images]
            parts.append(f"图片: {len(images)}张({', '.join(roles)})")
        if videos:
            parts.append(f"参考视频: {len(videos)}个")
        if audios:
            parts.append(f"参考音频: {len(audios)}个")
        return ", ".join(parts) if parts else "纯文生视频"

    @staticmethod
    def _compose_detail(
        prompt: str,
        ratio: str,
        resolution: str,
        requested_duration: int,
        input_summary: str,
        actual_duration: int | None = None,
    ) -> str:
        parts = [
            f"生成提示词: {prompt}",
            f"输入模式: {input_summary}",
            f"宽高比: {ratio}",
            f"分辨率: {resolution}",
            f"请求时长: {requested_duration}s",
        ]
        if actual_duration is not None and actual_duration != requested_duration:
            parts.append(f"实际时长: {actual_duration}s")
        return "\n".join(parts)


__all__ = ["VideoGenerationTool"]
