"""Web研究工具 - 自主搜索+抓取+综合分析."""

from __future__ import annotations

import json
import logging
from typing import ClassVar, override

from pydantic import BaseModel, ConfigDict, Field

from src.tools.experts.web_research.service import run_web_research
from src.tools.shared.base_expert_tool import BaseExpertTool

logger = logging.getLogger(__name__)


class WebResearchInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    query: str = Field(description="自然语言研究查询(支持包含URL)")
    depth: str = Field(
        default="deep",
        description="研究深度: quick(快速搜索,约5秒)/deep(深度研究,约60-200秒)",
    )
    language: str = Field(default="zh", description="回答语言: zh/en")


class WebResearchTool(BaseExpertTool):
    """Web研究工具 - 自主搜索+抓取+综合分析."""

    name: str = "web_research"
    summary: str = "网络搜索与研究, 搜索互联网获取实时信息, 返回带引用的结构化答案"
    search_keywords: ClassVar[list[str]] = [
        "搜索",
        "研究",
        "调查",
        "查找资料",
        "网络搜索",
        "深度研究",
    ]
    description: str = (
        "网络搜索与研究工具, 搜索互联网获取实时信息, 返回带引用的结构化答案.\n"
        "两种深度: quick(快速搜索, 约5秒) / deep(深度研究, 约60-200秒).\n"
        "默认使用deep深度. 如需分析用户提供的链接, 必须把完整URL放入query.\n\n"
        "- deep: 深度研究, 约60-200秒, Agent自主搜索+抓取+综合分析, 适合需要多源对比的复杂问题\n"
        "默认使用deep深度.\n\n"
        '示例: {"query": "比较 https://example.com/a 和 https://example.com/b", "depth": "quick"}'
    )
    args_schema: type[BaseModel] = WebResearchInput

    model_id: str = ""
    timeout: float = 360.0

    @override
    async def _arun(
        self,
        query: str,
        depth: str = "deep",
        language: str = "zh",
    ) -> str:
        try:
            from src.inference.usage import usage_source

            with usage_source("expert_llm"):
                result = await run_web_research(
                    query=query,
                    depth=depth,
                    language=language,
                    model_id=self.model_id,
                    timeout=self.timeout,
                    llm_request_timeout=90.0,
                    mcp_bridge=self.mcp_bridge,
                )

            if "error" in result:
                return json.dumps(
                    {
                        "error": result.get("error"),
                        "result": result.get("result", ""),
                    },
                    ensure_ascii=False,
                )

            return result.get("result", "")

        except Exception as e:
            logger.exception("WebResearchTool执行失败: %s", e)
            return json.dumps(
                {"error": str(e), "source": "web_research"},
                ensure_ascii=False,
            )
