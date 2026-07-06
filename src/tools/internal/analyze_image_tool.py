"""按需求读图工具 - 使用视觉模型重新识别图片内容."""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any, ClassVar, override

from pydantic import ConfigDict, Field

from src.files import AttachmentDTO
from src.inference.llm.response_utils import content_to_text
from src.tools.shared.base_internal_tool import BaseInternalTool
from src.tools.shared.query_alias_model import QueryAliasModel

logger = logging.getLogger(__name__)


class AnalyzeImageInput(QueryAliasModel):
    """图片分析输入."""

    _field_aliases: ClassVar[dict[str, str]] = {
        "image_id": "attachment_id",
        "file_id": "attachment_id",
    }

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    prompt: str = Field(
        min_length=1,
        max_length=4000,
        description=(
            "本次读图的具体要求, 必填. "
            "必须明确说明想从图片中获取什么信息, "
            "例如: 识别文字,查找细节,比较物体,判断表格内容,描述整体画面等"
        ),
    )
    attachment_id: str | None = Field(
        default=None,
        description=(
            "附件ID (8位hex). 从对话历史中的 [file: file_id] 标记提取 file_id; "
            "未提供时读取最近图片"
        ),
    )
    recent_index: int = Field(
        default=0,
        ge=0,
        le=20,
        description="未提供 attachment_id 时使用, 0=最近一张图片, 1=上一张图片",
    )
    max_chars: int = Field(
        default=4000,
        ge=200,
        le=12000,
        description="返回结果最大字符数",
    )


class AnalyzeImageTool(BaseInternalTool):
    """图片分析工具."""

    name: str = "analyze_image"
    summary: str = "按具体需求分析图片原图细节"
    search_keywords: ClassVar[list[str]] = [
        "读图",
        "OCR",
        "识别",
        "图片细节",
        "表格",
        "文字识别",
    ]
    description: str = (
        "按具体需求重新分析用户上传过的图片原图, 支持 OCR,表格提取,细节识别,数值读取等.\n"
        "使用视觉模型对原图进行针对性分析, 返回明确请求的具体信息.\n"
        "当用户的问题需要基于原图做特定分析(如识别文字,读取表格,判断内容)时使用.\n"
        "\n"
        "定位为非视觉模型的读图补充 (视觉模型主对话时本工具自动跳过). "
        "调用时必须提供 prompt, 明确说明想从图片中获取什么信息. "
        "优先使用 attachment_id 指定历史图片; 用户说'刚才那张图'且未提供 ID 时, "
        "可不传 attachment_id 并使用 recent_index=0.\n\n"
        '示例: {"attachment_id": "a1b2c3d4", "prompt": "逐字识别这张图里的收据金额和商户名"}\n'
        '示例: {"attachment_id": "a1b2c3d4", "prompt": "描述图片细节"}'
    )
    args_schema: type[AnalyzeImageInput] = AnalyzeImageInput

    @override
    async def _arun(
        self,
        prompt: str,
        attachment_id: str | None = None,
        recent_index: int = 0,
        max_chars: int = 4000,
    ) -> str:
        try:
            entry = await self._resolve_entry(attachment_id, recent_index)
            if not entry:
                return self._format_error(ValueError("未找到可读取的图片附件"))
            if entry.file_type != "image":
                return self._format_error(
                    ValueError(f"附件 {entry.file_id} 不是图片"),
                )

            image_path = self._resolve_image_path(entry)
            if not image_path.exists():
                return self._format_error(
                    FileNotFoundError(f"图片文件已不存在: {entry.filename}"),
                )

            mime_type = self._guess_mime_type(entry, image_path)
            result, model_id = await self._read_image(
                image_path,
                mime_type,
                prompt,
            )
            if not result.strip():
                return self._format_error(RuntimeError("视觉模型未返回有效结果"))

            result = result[:max_chars]
            return self._format_success(
                {
                    "attachment_id": entry.file_id,
                    "round_number": entry.round_number,
                    "filename": entry.filename,
                    "model_id": model_id,
                    "prompt": prompt,
                    "result": result,
                },
                message="图片读取完成",
            )

        except Exception as e:
            logger.exception("analyze_image 执行失败: %s", e)
            return self._format_error(e)

    async def _resolve_entry(
        self,
        attachment_id: str | None,
        recent_index: int,
    ) -> AttachmentDTO | None:
        if attachment_id:
            return await self._get_entry(attachment_id)
        return await self._get_recent_image(recent_index)

    async def _get_entry(self, file_id: str) -> AttachmentDTO | None:
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        registry = await create_file_registry_service(self.user_id)
        entry = await registry.get(file_id)
        if not entry:
            return None
        return self._entry_from_db(entry)

    async def _get_recent_image(self, recent_index: int) -> AttachmentDTO | None:
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        registry = await create_file_registry_service(self.user_id)
        entries = await registry.list_recent_images(limit=recent_index + 1)
        if len(entries) <= recent_index:
            return None

        return self._entry_from_db(entries[recent_index])

    @staticmethod
    def _entry_from_db(db_entry: Any) -> AttachmentDTO:
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

    def _resolve_image_path(self, entry: AttachmentDTO) -> Path:
        from src.core.path_resolver import resolve_attachment_internal_path

        return resolve_attachment_internal_path(
            entry.internal_path,
            self.user_id,
            self.thread_id,
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
        }
        return mime_map.get(suffix, "image/jpeg")

    async def _read_image(
        self,
        image_path: Path,
        mime_type: str,
        prompt: str,
    ) -> tuple[str, str]:
        """使用配置的视觉模型读取图片内容."""
        from src.config.inference_config import get_config as get_inference_config

        cfg = get_inference_config().image_description
        image_bytes = await asyncio.to_thread(image_path.read_bytes)
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        model = cfg.read_image_model or cfg.model
        params = cfg.read_image_model_params or cfg.model_params

        result = await self._call_vision_model(
            model,
            image_base64,
            mime_type,
            prompt,
            params,
        )
        return result, model

    async def _call_vision_model(
        self,
        model_id: str,
        image_base64: str,
        mime_type: str,
        prompt: str,
        params: dict[str, Any] | None,
    ) -> str:
        try:
            from langchain_core.messages import HumanMessage

            from src.inference.llm.model_loader import invoke_with_fallback

            message = HumanMessage(
                content=[
                    {"type": "text", "text": self._build_prompt(prompt)},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
                    },
                ],
            )
            response = await invoke_with_fallback(
                [message],
                model_id,
                params,
                fallback_kind="vision",
                usage_tag="vision_description",
                use_json_mode=False,
            )
            return self._extract_text(response.content)
        except Exception as e:
            logger.warning("视觉模型 %s 按需读图失败: %s", model_id, e)
            return ""

    @staticmethod
    def _build_prompt(prompt: str) -> str:
        return (
            "你是图片识别工具.请只根据图片内容回答用户的读图要求, "
            "不要编造图片中不存在的信息.如果看不清, 明确说明不确定.\n\n"
            f"用户读图要求:\n{prompt}"
        )

    @staticmethod
    def _extract_text(content: Any) -> str:
        return content_to_text(content).strip()


__all__ = ["AnalyzeImageInput", "AnalyzeImageTool"]
