"""重新生成下载链接工具 - 按 file_id 注册文件到 exported_files 供系统注入."""

from __future__ import annotations

import logging
from typing import ClassVar, override

from pydantic import ConfigDict, Field

from src.files import AttachmentDTO
from src.tools.shared.base_internal_tool import BaseInternalTool
from src.tools.shared.query_alias_model import QueryAliasModel

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"})


class RegenerateDownloadLinkInput(QueryAliasModel):
    """重新生成下载链接输入."""

    _field_aliases: ClassVar[dict[str, str]] = {
        "attachment_id": "file_id",
    }

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    file_id: str = Field(
        description="文件ID (8位hex). 从对话历史中的 [file: file_id] 标记提取 file_id",
    )


class RegenerateDownloadLinkTool(BaseInternalTool):
    """重新生成下载链接工具."""

    name: str = "regenerate_download_link"
    summary: str = "为历史文件准备下载链接, 系统会自动发送给用户"
    search_keywords: ClassVar[list[str]] = [
        "下载",
        "下载链接",
        "重新生成链接",
        "获取链接",
    ]
    description: str = (
        "为历史文件准备下载链接, 当用户需要下载或重新发送之前生成的文件时使用.\n"
        "对话历史中的文件以 [file: file_id] 标记存储, 调用时从标记提取 file_id. "
        "调用此工具后系统会自动将下载链接发送给用户, 不需要在回复中包含任何URL.\n\n"
        '示例: 用户说"重新发送 [file: a1b2c3d4]" -> 调用 {"file_id": "a1b2c3d4"}\n'
        '示例: {"file_id": "a1b2c3d4"}'
    )
    args_schema: type[RegenerateDownloadLinkInput] = RegenerateDownloadLinkInput

    @override
    async def _arun(self, file_id: str) -> str:
        try:
            entry = await self._get_entry(file_id)
            if not entry:
                return self._format_error(
                    ValueError(f"文件 {file_id} 不存在或已过期"),
                )

            from src.core.path_resolver import resolve_attachment_internal_path

            full_path = resolve_attachment_internal_path(
                entry.internal_path,
                self.user_id,
                self.thread_id,
            )

            if not full_path.exists():
                return self._format_error(
                    FileNotFoundError(f"文件已不存在: {entry.filename}"),
                )

            from src.config.api_config import get_config as get_api_config
            from src.files.signed_url import get_signed_url_provider

            token = get_signed_url_provider().compose_token(
                self.user_id,
                self.thread_id,
                self.agent_id,
                file_id,
            )

            base_url = get_api_config().get_file_server_url()
            url = (
                f"{base_url}/"
                f"{self.user_id}/{self.thread_id}/{self.agent_id}/"
                f"{token}/{entry.filename}"
            )

            file_type = (
                "image"
                if (entry.file_format or "").lower() in _IMAGE_EXTENSIONS
                else entry.file_type
            )
            self._register_exported_file(url, file_id, file_type, entry)

            return self._format_success(
                {
                    "file_id": file_id,
                    "filename": entry.filename,
                    "format": entry.file_format,
                    "size_bytes": entry.file_size,
                },
                message=f"文件下载链接已准备: [file: {file_id}] {entry.filename}, 请直接告知用户文件已准备好",
            )

        except Exception as e:
            logger.error("regenerate_download_link 执行失败: %s", e)
            return self._format_error(e)

    def _register_exported_file(
        self, url: str, file_id: str, file_type: str, entry: AttachmentDTO
    ) -> None:
        """将签名URL注册到 exported_files, 供系统层自动注入到响应末尾."""
        from src.core.context import get_user_context_or_none

        ctx = get_user_context_or_none()
        if not ctx:
            return
        ctx.exported_files.append({
            "url": url,
            "file_id": file_id,
            "file_type": file_type,
            "brief": entry.brief,
            "internal_path": entry.internal_path,
            "filename": entry.filename,
            "size_bytes": entry.file_size,
            "format": entry.file_format,
            "content_hash": entry.content_hash,
        })

    async def _get_entry(self, file_id: str) -> AttachmentDTO | None:
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
