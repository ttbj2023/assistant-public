"""地理出行研究工具 - 基于Gemini Maps Grounding + 百度地图API."""

from __future__ import annotations

import json
import logging
from typing import ClassVar, override

from pydantic import BaseModel, ConfigDict, Field

from src.tools.experts.geo_research.service import run_geo_research
from src.tools.shared.base_expert_tool import BaseExpertTool

logger = logging.getLogger(__name__)


class GeoResearchInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    query: str = Field(description="自然语言地理/出行查询")
    depth: str = Field(
        default="quick",
        description=(
            "研究深度: "
            "quick(快速, Gemini一步回答, 约3秒, 适合简单查询), "
            "deep(深度, Gemini+百度API补充核实, 约15秒, 适合复杂出行规划)"
        ),
    )
    language: str = Field(default="zh", description="回答语言: zh/en")


class GeoResearchTool(BaseExpertTool):
    """地理出行研究工具 - Gemini Maps + 百度地图."""

    name: str = "geo_navigator"
    summary: str = "地图导航与POI搜索, 查附近地点/规划路线/查路况"
    search_keywords: ClassVar[list[str]] = [
        "地图",
        "导航",
        "路线",
        "出行",
        "位置",
        "POI",
        "餐厅",
        "周边",
        "附近",
    ]
    description: str = (
        "地理出行研究工具, 接收自然语言地理/出行查询.\n"
        "支持: 搜索POI/规划驾车公交步行路线/查实时路况/距离计算.\n"
        "搜索POI,规划路线,查询实时路况等.\n"
        "支持两种深度:\n"
        "- quick(默认): 快速回答, 约3秒, 适合简单查询\n"
        "- deep: 深度研究, 约15秒, 用百度API补充实时路况/精确路线\n\n"
        '示例: {"query": "北京南站附近有什么好吃的", "depth": "quick"}\n'
        '示例: {"query": "从北京到上海自驾怎么走", "depth": "deep"}'
    )
    args_schema: type[BaseModel] = GeoResearchInput

    model_id: str = ""
    timeout: float = 120.0

    @override
    async def _arun(
        self,
        query: str,
        depth: str = "quick",
        language: str = "zh",
    ) -> str:
        try:
            from src.inference.usage import usage_source

            with usage_source("expert_llm"):
                result = await run_geo_research(
                    query=query,
                    depth=depth,
                    language=language,
                    model_id=self.model_id,
                    timeout=self.timeout,
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
            logger.exception("GeoResearchTool执行失败: %s", e)
            return json.dumps(
                {"error": str(e), "source": "geo_navigator"},
                ensure_ascii=False,
            )
