"""文档导出外部工具 - 模板驱动的文档导出.

设计约定:
- External Tool, 无状态全局共享 (文件保存通过 get_user_context() 获取用户路径)
- LLM 不参与格式化, PDF 由 YAML 模板 + CSS 生成器承担; DOCX 由 pandoc reference-doc 承担
- 支持 PDF/DOCX 两种输出格式
- 渲染基础设施: pandoc + Chromium (Playwright)
"""

from __future__ import annotations

import json
import logging
import re
from typing import ClassVar, Literal, override

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.tools.shared.base_external_tool import BaseExternalTool
from src.tools.shared.query_alias_model import QueryAliasModel

logger = logging.getLogger(__name__)

OutputFormat = Literal["pdf", "docx"]

# 自动生成文件名: 从内容首个标题清洗, 截断长度, 无标题时回退值
_AUTOGEN_MAX = 50
_FALLBACK_FILENAME = "document"
_H1_PREFIX = "# "


def _resolve_filename(content: str, filename: str | None) -> str:
    """filename 为空时从内容首个 # 标题自动生成.

    生成的文件名只含 [\\w\\-.] (\\w 含中文), 保证通过 service._validate_filename
    的二次清洗校验 (恒等). 显式提供的 filename 原样返回 (交由 service 校验).
    """
    if filename and filename.strip():
        return filename.strip()
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(_H1_PREFIX):
            raw = stripped[len(_H1_PREFIX) :].strip()
            safe = re.sub(r"[^\w\-.]", "_", raw)[:_AUTOGEN_MAX].strip("_.-")
            if safe:
                return safe
    return _FALLBACK_FILENAME


class ExportDocumentInput(QueryAliasModel):
    """文档导出输入."""

    _field_aliases: ClassVar[dict[str, str]] = {
        "query": "content",
        "markdown_content": "content",
        "title": "filename",
        "file_name": "filename",
        "file_type": "format",
        "type": "format",
    }

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    content: str = Field(
        min_length=1,
        max_length=500000,
        description="Markdown格式的文档内容",
    )
    style: str = Field(
        default="default",
        max_length=50,
        description=(
            "视觉风格/模板名. 可选: default, academic, business, "
            "technical. 留空使用默认排版"
        ),
    )
    format: OutputFormat = Field(
        default="pdf",
        description="输出格式: pdf 或 docx",
    )
    filename: str | None = Field(
        default=None,
        max_length=200,
        description="输出文件名(不含扩展名), 留空则从内容标题自动生成",
    )

    @field_validator("format", mode="before")
    @classmethod
    def normalize_format(cls, v: str) -> str:
        """将格式统一转为小写, 兼容 LLM 传入 'PDF'/'Pdf' 等大小写变体."""
        if isinstance(v, str):
            return v.lower()
        return v


class ExportDocumentTool(BaseExternalTool):
    """文档导出工具 - 将 Markdown 内容按模板渲染为 PDF/DOCX."""

    name: str = "export_document"
    summary: str = (
        "文档导出工具: 将 Markdown 内容渲染为 PDF/DOCX 文档, 支持报告/方案/总结等"
    )
    search_keywords: ClassVar[list[str]] = [
        "导出",
        "PDF",
        "文档",
        "转换",
        "报告",
        "DOCX",
        "设计",
    ]
    description: str = (
        # 前 3 行: 纯能力描述, 供工具筛选/匹配 (被 _llm_tool_filter 截取喂给小模型)
        "文档导出工具, 将 Markdown 内容按视觉风格渲染为 PDF/DOCX 文档.\n"
        "适用: 月度报告,技术方案,会议总结,产品文档等结构化长文导出.\n"
        "支持 4 种风格模板(default/academic/business/technical)与 PDF/DOCX 格式.\n"
        # 第 4 行起: 参数细节, 仅主对话 agent 实际调用时可见
        "\n"
        "content(必填)为完整 GFM Markdown(表格/脚注/Callout(:::tip)/代码高亮等); "
        "内部预处理器还会把 mermaid/vega-lite/markmap 代码块渲染为图,支持 $...$ LaTeX 数学公式.\n"
        "其余参数可选 — style(风格), format(pdf/docx), filename(留空则从内容标题自动生成).\n\n"
        '示例: {"content": "# 月度报告\\n## 概要\\n...", "style": "business", "format": "pdf"}'
    )
    args_schema: type[BaseModel] = ExportDocumentInput
    timeout: float = 120.0

    @override
    async def _arun(
        self,
        content: str,
        style: str = "default",
        format: str = "pdf",
        filename: str | None = None,
    ) -> str:
        try:
            from src.core.context import get_user_context

            from .service import run_export_document

            # filename 留空时从内容标题自动生成, 保证非空且合法
            filename = _resolve_filename(content, filename)

            ctx = get_user_context()
            result = await run_export_document(
                content=content,
                style=style,
                output_format=format,
                filename=filename,
                user_id=ctx.user_id,
                thread_id=ctx.thread_id,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.exception("ExportDocumentTool 执行失败: %s", e)
            return self._format_error(e)


__all__ = ["ExportDocumentInput", "ExportDocumentTool"]
