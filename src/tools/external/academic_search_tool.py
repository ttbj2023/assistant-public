"""学术搜索工具 - web_research内部工具, 调用DataPro学术论文数据集.

定位: web_research deep模式的内部工具, 供Agent编排使用.
与professional_database的区别: academic_search专精学术论文, 只在web_research内部使用,
不暴露给主对话; professional_database面向主对话的结构化数据(金融/工商/风险).

DataPro学术返回结构(5篇论文, 每篇6字段):
- name: 论文标题
- url: 论文/PDF链接
- date_published: 发表日期(ISO)
- snippet: 搜索摘要片段
- abstract: 论文完整摘要(核心价值, 部分为空)
- extra_data: {authors, cite_by(引用数), doi, journal_title, ...}

返回大小5-12K, 结构干净, 无需复杂预处理/DeepSeek整理.
"""

from __future__ import annotations

import json
import logging
from typing import Any, override

from pydantic import BaseModel, ConfigDict, Field

from src.config.credentials_registry import get_credential
from src.tools.shared.base_external_tool import BaseExternalTool

logger = logging.getLogger(__name__)

_DATAPRO_URL = "https://datapro.hqd.cn-beijing.volces.com/mcp"
_DATAPRO_TOOL_NAME = "dataPro_search"
_DATAPRO_CALL_TIMEOUT = 60.0


class AcademicSearchInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    query: str = Field(
        description="学术论文搜索查询: 学科关键词/论文标题/作者+研究方向/技术主题.",
    )


class AcademicSearchTool(BaseExternalTool):
    """学术论文搜索工具 - 调用DataPro学术论文数据集."""

    name: str = "academic_search"
    summary: str = "学术论文搜索, 返回论文标题/作者/引用数/摘要/链接"
    description: str = (
        "学术论文搜索工具, 查询英文学术论文(数据源: 火山引擎DataPro学术数据集).\n"
        "返回5篇最相关论文, 含标题/作者/发表日期/引用数/摘要/链接/DOI.\n\n"
        "适用场景: 需要权威学术研究支撑的技术/科学问题, 区别于普通网络搜索的二手信息.\n"
        "query 支持多种形式: 学科关键词(如'transformer attention')/"
        "论文标题/作者+研究方向.\n\n"
        '示例: {"query": "large language model"}\n'
        '示例: {"query": "Attention Is All You Need"}\n'
        '示例: {"query": "Yoshua Bengio deep learning"}'
    )
    args_schema: type[BaseModel] = AcademicSearchInput

    datapro_url: str = Field(
        default=_DATAPRO_URL,
        description="DataPro MCP服务地址",
    )
    api_key_env: str = Field(
        default="ARK_AGENT_PLAN_API_KEY",
        description="DataPro API Key的环境变量名",
    )

    @override
    async def is_available(self) -> bool:
        return bool(_get_datapro_api_key(self.api_key_env))

    @override
    async def _arun(self, query: str) -> str:
        api_key = _get_datapro_api_key(self.api_key_env)
        if not api_key:
            return f"[配置缺失: 环境变量{self.api_key_env}未设置]"

        from fastmcp.client import Client
        from fastmcp.client.transports import StreamableHttpTransport

        headers = {"X-Agent-Plan-Key": api_key}
        transport = StreamableHttpTransport(url=self.datapro_url, headers=headers)
        try:
            async with Client(transport, timeout=_DATAPRO_CALL_TIMEOUT) as client:
                result = await client.call_tool(_DATAPRO_TOOL_NAME, {"query": query})
            text = _extract_result_text(result)
            return _format_papers(text, query)
        except TimeoutError:
            return f"[查询超时({_DATAPRO_CALL_TIMEOUT}秒)]"
        except Exception as e:
            logger.exception("学术搜索失败: %s", e)
            return f"[查询失败: {e}]"


def _extract_result_text(result: Any) -> str:
    """从fastmcp CallToolResult提取文本."""
    if isinstance(result, str):
        return result

    content = getattr(result, "content", None)
    if content is None and isinstance(result, (list, tuple)):
        content = result

    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict) and "text" in item:
                texts.append(item["text"])
            elif hasattr(item, "text"):
                texts.append(item.text)
        return "\n".join(texts)

    return str(result) if result else ""


def _get_datapro_api_key(env_name: str) -> str:
    """读取 DataPro API Key."""
    if env_name == "ARK_AGENT_PLAN_API_KEY":
        return get_credential("ark_agent_plan_api_key")
    return ""


def _format_papers(text: str, query: str) -> str:
    """格式化学术论文JSON为易读文本.

    论文摘要完整保留(核心价值), 不截断.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text

    items = data.get("items", []) if isinstance(data, dict) else []
    if not items:
        return f"[未找到与'{query}'相关的学术论文]"

    lines = [f"学术论文搜索结果 (query: {query}, 共{len(items)}篇):"]
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue

        name = item.get("name", "未知标题")
        url = item.get("url", "")
        date_raw = item.get("date_published", "") or ""
        date = date_raw[:4] if date_raw else ""
        snippet = item.get("snippet", "")
        abstract = item.get("abstract", "")
        extra = item.get("extra_data")
        if not isinstance(extra, dict):
            extra = {}

        authors = extra.get("authors", "")
        cite_by = extra.get("cite_by")
        doi = extra.get("doi", "")
        journal = extra.get("journal_title", "")

        summary = abstract if abstract else snippet

        lines.append("")
        lines.append(f"[论文{i}]{name}")
        if authors:
            lines.append(f"  作者: {authors}")
        meta_parts: list[str] = []
        if date:
            meta_parts.append(date)
        if cite_by is not None:
            meta_parts.append(f"引用{cite_by}")
        if journal:
            meta_parts.append(journal)
        if meta_parts:
            lines.append(f"  {' | '.join(meta_parts)}")
        if summary:
            lines.append(f"  摘要: {summary}")
        if url:
            lines.append(f"  链接: {url}")
        if doi:
            lines.append(f"  DOI: {doi}")

    return "\n".join(lines)


__all__ = ["AcademicSearchTool"]
