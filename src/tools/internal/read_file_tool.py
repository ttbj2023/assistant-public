"""文件描述读取工具 - 按 file_id 读取 .desc.md 描述文件 (摘要/行级双模式).

每个文件配套一个 .desc.md 描述文件, 统一结构为 摘要 + 分隔符 + 原文/源码:
- 原文/源码: 上传文件=原文 (图片=画面描述), 生成文件=源码/生成参数
本工具读取该描述, 供 LLM 理解文件内容, 无需直接处理二进制文件.

读取模式:
- 摘要模式 (默认, 仅传 file_id): 返回摘要区 + 原文区起始行号
- 行级模式 (传 start_line): 按行读取窗口, next_start_line 续读

定位: 通用文件描述读取, 所有模型可用 (轻量, 不调用视觉模型).
视觉模型按需重新分析原图请用 analyze_image 工具.
"""

from __future__ import annotations

import logging
from typing import ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import ConfigDict, Field

from src.files import AttachmentDTO
from src.tools.shared.query_alias_model import QueryAliasModel
from src.tools.shared.tool_runtime import (
    format_tool_error,
    format_tool_success,
    sync_runnable,
)

logger = logging.getLogger(__name__)

# 单次响应内部字符上限: 防超长单行文件 (压缩JS/base64等) 撑爆上下文
_MAX_RESPONSE_CHARS = 50000
# 单行硬截断标记 (病态文件: 单行超过整个响应上限)
_LINE_TRUNCATION_MARK = "[此行超长已截断]"


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
    start_line: int | None = Field(
        default=None,
        ge=1,
        description=(
            "起始行号 (1-based). 不传=返回摘要; "
            "传=按行读取原文/源码 (首个行号用摘要响应的 original_start_line)"
        ),
    )
    max_lines: int = Field(
        default=200,
        ge=1,
        le=1000,
        description="行级模式单次返回的最大行数",
    )


@sync_runnable
class ReadFileTool(BaseTool):
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
        "按文件ID读取文件描述, 默认返回摘要 (图片概要/文档主题/代码结构等).\n"
        "需要原文/源码细节时, 用摘要响应中的 original_start_line 作为 start_line 按行读取; "
        "未读完用响应中的 next_start_line 续读, 不要重复请求已读区间.\n"
        "单次响应上限约50000字符, 达到时会提前停止并在 note 说明.\n\n"
        "调用时从对话历史的 [file: file_id] 标记提取 file_id.\n"
        '示例: {"file_id": "a1b2c3d4"} → 摘要\n'
        '示例: {"file_id": "a1b2c3d4", "start_line": 5} → 从第5行起读200行'
    )
    args_schema: type[ReadFileInput] = ReadFileInput

    @override
    async def _arun(
        self,
        file_id: str,
        start_line: int | None = None,
        max_lines: int = 200,
    ) -> str:
        try:
            from src.files.desc_writer import read_desc, split_desc

            # 读 .desc.md 描述文件 (权威描述: 摘要+原文/源码 统一结构)
            content = read_desc(self.user_id, file_id)

            # 查注册表获取元信息 (filename/file_type)
            entry = await self._get_entry(file_id)

            if not content:
                return format_tool_error(
                    ValueError(f"文件 {file_id} 无可用描述 (描述文件未生成)"),
                )

            lines = content.splitlines()
            total_lines = len(lines)
            summary, original = split_desc(content)

            if start_line is None:
                return self._summary_response(
                    file_id,
                    entry,
                    summary,
                    original,
                    total_lines,
                )
            return self._line_window_response(
                file_id,
                entry,
                lines,
                total_lines,
                start_line,
                max_lines,
            )

        except Exception as e:
            logger.exception("read_file 执行失败: %s", e)
            return format_tool_error(e)

    def _summary_response(
        self,
        file_id: str,
        entry: AttachmentDTO | None,
        summary: str,
        original: str,
        total_lines: int,
    ) -> str:
        """构造摘要模式响应 (默认模式)."""
        has_original = bool(original)
        # 旧格式 desc (统一结构前写入) 无摘要区, 回退 DB brief
        note = None
        if not summary and has_original:
            summary = (entry.brief if entry else "") or ""
            note = "该文件描述为旧格式 (无结构化摘要), 原文从第 1 行开始"

        original_lines = len(original.splitlines()) if original else 0
        original_start_line = total_lines - original_lines + 1 if original else None

        return format_tool_success(
            {
                "mode": "summary",
                "file_id": file_id,
                "filename": entry.filename if entry else None,
                "file_type": entry.file_type if entry else None,
                "round_number": entry.round_number if entry else None,
                "summary": summary,
                "has_original": has_original,
                "original_start_line": original_start_line,
                "total_lines": total_lines,
                "note": note,
            },
            message="文件摘要读取完成",
        )

    def _line_window_response(
        self,
        file_id: str,
        entry: AttachmentDTO | None,
        lines: list[str],
        total_lines: int,
        start_line: int,
        max_lines: int,
    ) -> str:
        """构造行级窗口响应 (分页读取原文/源码)."""
        meta = {
            "file_id": file_id,
            "filename": entry.filename if entry else None,
            "file_type": entry.file_type if entry else None,
            "round_number": entry.round_number if entry else None,
        }

        # 越界: 不报错 (报错会诱导模型重试浪费轮次), 返回空窗口
        if start_line > total_lines:
            return format_tool_success(
                {
                    **meta,
                    "mode": "lines",
                    "content": "",
                    "source": "desc_file",
                    "total_lines": total_lines,
                    "returned_lines": 0,
                    "start_line": start_line,
                    "end_line": None,
                    "next_start_line": None,
                    "has_more": False,
                    "note": f"已越过文件末尾 (共 {total_lines} 行)",
                },
                message="读取区间为空",
            )

        window: list[str] = []
        accumulated = 0
        note = None
        end_line = start_line
        for idx in range(start_line - 1, total_lines):
            line = lines[idx]
            # 字符安全阀: 累计将超上限时停止攒行
            if window and accumulated + len(line) + 1 > _MAX_RESPONSE_CHARS:
                note = (
                    f"已达单次响应字符上限({_MAX_RESPONSE_CHARS}), "
                    "本次提前停止, 请从 next_start_line 续读"
                )
                break
            # 病态单行: 整行超过上限时硬截断 (行内剩余部分无法用行级API表达)
            if len(line) + len(_LINE_TRUNCATION_MARK) > _MAX_RESPONSE_CHARS:
                keep = _MAX_RESPONSE_CHARS - len(_LINE_TRUNCATION_MARK)
                line = line[:keep] + _LINE_TRUNCATION_MARK
                note = "单行超过响应字符上限, 已硬截断该行 (该行剩余部分无法读取)"
            window.append(line)
            accumulated += len(line) + 1
            end_line = idx + 1
            if len(window) >= max_lines:
                break

        has_more = end_line < total_lines
        return format_tool_success(
            {
                **meta,
                "mode": "lines",
                "content": "\n".join(window),
                "source": "desc_file",
                "total_lines": total_lines,
                "returned_lines": len(window),
                "start_line": start_line,
                "end_line": end_line,
                "next_start_line": end_line + 1 if has_more else None,
                "has_more": has_more,
                "note": note,
            },
            message="文件内容读取完成",
        )

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
