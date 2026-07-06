"""图片生成工具 - 将图片生成模型封装为主对话工具."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, ClassVar, Literal, override

from pydantic import ConfigDict, Field

from src.core.path_resolver import get_user_path_resolver
from src.inference.image_generation import ImageGenerationService
from src.tools.shared.base_internal_tool import BaseInternalTool
from src.tools.shared.query_alias_model import QueryAliasModel

logger = logging.getLogger(__name__)

SUPPORTED_SIZES = {
    "2048x2048",
    "2560x1440",
    "1440x2560",
    "3840x2160",
    "2160x3840",
}


class ImageGenerationInput(QueryAliasModel):
    """图片生成输入."""

    _field_aliases: ClassVar[dict[str, str]] = {"query": "prompt"}

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    prompt: str = Field(
        min_length=1,
        max_length=4000,
        description="图片生成提示词, 需要明确主体,风格,构图,颜色和画幅要求, 风格描述请嵌入提示词中",
    )
    size: Literal[
        "2048x2048",
        "2560x1440",
        "1440x2560",
        "3840x2160",
        "2160x3840",
    ] = Field(default="2048x2048", description="图片尺寸")
    filename: str | None = Field(
        default=None,
        max_length=120,
        description="输出文件名(不含扩展名), 可选",
    )


class ImageGenerationTool(BaseInternalTool):
    """图片生成工具."""

    name: str = "generate_image"
    summary: str = "AI图片生成, 根据文字描述画图/生成海报, 返回下载链接"
    search_keywords: ClassVar[list[str]] = [
        "画图",
        "绘图",
        "插图",
        "海报",
        "头像",
        "文生图",
        "作图",
    ]
    description: str = (
        "AI图片生成工具, 根据文字描述生成图片并返回下载链接. "
        "风格描述(如写实/赛博朋克/水彩/油画/插画等)请嵌入prompt中, 默认2048x2048.\n"
        "适用于用户明确要求画图,生成图片,制作插图/海报/头像/视觉稿等场景. "
        "调用前应将用户需求整理为具体英文或中文提示词, 包含主体,风格,构图,颜色,画幅和限制条件.\n\n"
        '示例: {"prompt": "一只橘猫坐在窗边, 水彩风格, 柔和晨光", "size": "2048x2048"}'
    )
    args_schema: type[ImageGenerationInput] = ImageGenerationInput

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        *,
        model_id: str = "",
        timeout: float = 120.0,
        agent_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(user_id, thread_id, agent_id=agent_id, **kwargs)
        if not model_id:
            from src.config.inference_config import get_config as get_inference_config

            model_id = get_inference_config().image_generation.model_id
        object.__setattr__(self, "_model_id", model_id)
        object.__setattr__(self, "_timeout", timeout)
        object.__setattr__(self, "_service", ImageGenerationService())

    @override
    async def _arun(
        self,
        prompt: str,
        size: str = "2048x2048",
        filename: str | None = None,
    ) -> str:
        try:
            self._validate_size(size)
            safe_filename = self._build_filename(filename)

            generated = await self._service.generate_image(
                model_id=self._model_id,
                prompt=prompt,
                size=size,
                watermark=False,
                timeout=self._timeout,
            )

            from src.files.paths import FILES_IMAGES_GENERATED
            from src.tools.shared.file_output import register_tool_output

            image_dir = get_user_path_resolver().get_shared_storage_path(
                self.user_id,
                self.thread_id,
                FILES_IMAGES_GENERATED,
            )
            output_path = image_dir / safe_filename
            output_path.write_bytes(generated.image_data)

            brief = self._compose_brief(prompt, size)
            detail = self._compose_detail(prompt, size, generated.revised_prompt)

            reg_result = await register_tool_output(
                output_path=output_path,
                display_filename=safe_filename,
                output_filename=safe_filename,
                output_format="png",
                file_type="image",
                content=detail,
                summary=brief,
                user_id=self.user_id,
                thread_id=self.thread_id,
            )

            return self._format_success(
                {
                    "file_id": reg_result["file_id"],
                    "filename": reg_result["filename"],
                    "format": "png",
                    "size": size,
                    "size_bytes": reg_result["size_bytes"],
                },
                message=f"图片已生成: [file: {reg_result['file_id']}] {safe_filename}",
            )

        except Exception as e:
            logger.exception("ImageGenerationTool 执行失败: %s", e)
            return self._format_error(e)

    @staticmethod
    def _validate_size(size: str) -> None:
        if size not in SUPPORTED_SIZES:
            raise ValueError(f"不支持的图片尺寸: {size}")

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
            stem = f"image_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.urandom(4).hex()}"

        if len(stem) > 120:
            raise ValueError("文件名过长, 最大120字符")
        return f"{stem}.png"

    @staticmethod
    def _compose_brief(prompt: str, size: str) -> str:
        normalized = " ".join(prompt.split())
        brief = normalized[:60]
        return f"生成图片 {size}: {brief}"

    @staticmethod
    def _compose_detail(
        prompt: str,
        size: str,
        revised_prompt: str | None = None,
    ) -> str:
        parts = [
            f"生成提示词: {prompt}",
            f"图片尺寸: {size}",
        ]
        if revised_prompt:
            parts.append(f"模型优化提示词: {revised_prompt}")
        return "\n".join(parts)


__all__ = ["ImageGenerationTool"]
