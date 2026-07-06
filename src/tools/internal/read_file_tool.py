"""文件描述读取工具 - 按 file_id 读取文件描述内容 (.desc.md).

每个文件配套一个 .desc.md 描述文件 (图片=画面描述, 文档=摘要).
本工具读取该描述, 供 LLM 理解文件内容, 无需直接处理二进制文件.

读取: .desc.md 描述文件 (权威描述; 图片=画面描述, 文档=摘要, chart/xlsx=源码)

定位: 通用文件描述读取, 所有模型可用 (轻量, 不调用视觉模型).
视觉模型按需重新分析原图请用 analyze_image 工具.
"""

from __future__ import annotations

import logging
from typing import ClassVar, override

from pydantic import ConfigDict, Field

from src.files import AttachmentDTO
from src.tools.shared.base_internal_tool import BaseInternalTool
from src.tools.shared.query_alias_model import QueryAliasModel

logger = logging.getLogger(__name__)


class ReadFileInput(QueryAliasModel):
    """文件描述读取输入."""

    _field_aliases: ClassVar[dict[str, str]] = {
        "attachment_id": "file_id",
    }

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    file_id: str = Field(
        description=(
            "文件ID (8位hex). 从对话历史中的 [file: file_id] 标记提取 file_id"
        ),
    )
    max_chars: int = Field(
        default=4000,
        ge=200,
        le=50000,
        description="返回描述的最大字符数",
    )


class ReadFileTool(BaseInternalTool):
    """文件描述读取工具."""

    name: str = "read_file"
    summary: str = "按文件ID读取文件描述内容"
    search_keywords: ClassVar[list[str]] = [
        "读取文件",
        "文件描述",
        "文件内容",
        "查看文件",
        "图片描述",
        "文档摘要",
    ]
    description: str = (
        "按文件ID读取文件描述内容, 返回图片画面描述/文档摘要/chart与xlsx源码.\n"
        "适用于需要回顾历史文件内容,理解文件主题的场景, 所有模型可用 (轻量, 不调用视觉模型).\n"
        "视觉模型按需重新分析图片原图细节 (OCR/表格/数值) 请用 analyze_image 工具.\n"
        "源码类描述较长时, 默认返回前4000字符并标记 truncated=true, 可调大 max_chars 读全文.\n\n"
        "调用时从对话历史的 [file: file_id] 标记提取 file_id.\n"
        '示例: {"file_id": "a1b2c3d4"}'
    )
    args_schema: type[ReadFileInput] = ReadFileInput

    @override
    async def _arun(
        self,
        file_id: str,
        max_chars: int = 4000,
    ) -> str:
        try:
            from src.files.desc_writer import read_desc

            # 读 .desc.md 描述文件 (权威描述; 图片=画面描述, 文档=摘要, chart/xlsx=源码)
            content = read_desc(self.user_id, file_id)
            source = "desc_file"

            # 查注册表获取元信息 (filename/file_type)
            entry = await self._get_entry(file_id)

            if not content:
                return self._format_error(
                    ValueError(f"文件 {file_id} 无可用描述 (描述文件未生成)"),
                )

            content_total_chars = len(content)
            truncated = content_total_chars > max_chars
            content = content[:max_chars]
            return self._format_success(
                {
                    "file_id": file_id,
                    "filename": entry.filename if entry else None,
                    "file_type": entry.file_type if entry else None,
                    "round_number": entry.round_number if entry else None,
                    "content": content,
                    "source": source,
                    "truncated": truncated,
                    "content_total_chars": content_total_chars,
                },
                message="文件描述读取完成",
            )

        except Exception as e:
            logger.exception("read_file 执行失败: %s", e)
            return self._format_error(e)

    async def _get_entry(self, file_id: str) -> AttachmentDTO | None:
        """查询文件注册表获取元信息 (filename/file_type)."""
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        registry = await create_file_registry_service(self.user_id)
        entry = await registry.get(file_id)
        if not entry:
            return None

        internal_path = (
            entry.physical_path.split("shared/", 1)[-1]
            if "shared/" in entry.physical_path
            else entry.physical_path
        )
        return AttachmentDTO(
            file_id=entry.file_id,
            file_type=entry.file_type,
            internal_path=internal_path,
            filename=entry.filename,
            brief=entry.brief,
            detail="",
            file_format=entry.file_format,
            file_size=entry.file_size,
            content_hash=entry.content_hash,
            round_number=entry.round_number,
        )


__all__ = ["ReadFileInput", "ReadFileTool"]
