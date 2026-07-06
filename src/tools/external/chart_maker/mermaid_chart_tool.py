"""mermaid 流程图渲染子工具 (chart_maker 技能关联工具).

固定 engine=mermaid, 仅渲染流程图/时序图为 PNG (其他 mermaid 图类型渲染质量不稳定,
饼图/数据图表请用 vega_chart). 不暴露 width/height (mermaid 引擎忽略尺寸).
"""

from __future__ import annotations

from typing import ClassVar, override

from .chart_maker_base import ChartMakerBase
from .models import MermaidChartInput


class MermaidChartTool(ChartMakerBase):
    """渲染 mermaid 流程图/时序图为 PNG 图片."""

    engine: ClassVar[str] = "mermaid"
    name: str = "mermaid_chart"
    summary: str = "渲染mermaid流程图/时序图为PNG图片"
    description: str = (
        "渲染 mermaid 流程图/时序图为 PNG 图片.\n"
        "code 必须是 mermaid 语法源码 (如 'graph TD\\nA-->B'), 不能是自然语言描述.\n"
        "仅支持: flowchart (流程图/架构图/状态流转), sequenceDiagram (时序图).\n"
        "饼图/数据统计图表请用 vega_chart.\n"
        "filename 可选, 留空时根据 title 自动生成.\n"
        "scale 控制清晰度: 简单图(少量节点)用3, 复杂图(多分支/多节点/密集文字)用5."
    )
    args_schema: type = MermaidChartInput

    @override
    async def _arun(
        self,
        code: str,
        filename: str | None = None,
        title: str | None = None,
        scale: int = 3,
    ) -> str:
        return await self._render(
            code=code, filename=filename, title=title, scale=scale
        )


__all__ = ["MermaidChartTool"]
