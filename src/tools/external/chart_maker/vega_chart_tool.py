"""Vega-Lite 数据图表渲染子工具 (chart_maker 技能关联工具).

固定 engine=vega_lite, 渲染折线/柱状/饼/散点/堆叠等统计图表为 PNG.
"""

from __future__ import annotations

from typing import ClassVar, override

from langchain_core.tools import BaseTool

from src.tools.shared.tool_runtime import sync_runnable

from .chart_render import render_chart
from .models import VegaChartInput


@sync_runnable
class VegaChartTool(BaseTool):
    """渲染 Vega-Lite 数据图表为 PNG 图片."""

    engine: ClassVar[str] = "vega_lite"
    name: str = "vega_chart"
    summary: str = "渲染Vega-Lite数据图表(折线/柱状/饼/散点/堆叠)为PNG图片"
    description: str = (
        "渲染 Vega-Lite 数据图表为 PNG 图片.\n"
        "code 必须是完整的 Vega-Lite JSON 规范字符串 "
        '(如 \'{"mark":"bar","encoding":{"x":{...},"y":{...}}}\'), '
        "不能是自然语言描述.\n"
        "支持折线/柱状/饼/散点/堆叠/面积等统计图表.\n"
        "width/height 注入 spec 覆盖原值 (px).\n"
        "scale 控制清晰度 (1标准/3默认高清/6最大).\n"
        "nominal/ordinal 类别轴默认斜向旋转 45 度 (标签少时保持水平), "
        "自定义角度在 encoding.x.axis.labelAngle 指定."
    )
    args_schema: type = VegaChartInput

    @override
    async def _arun(
        self,
        code: str,
        filename: str | None = None,
        title: str | None = None,
        width: int | None = None,
        height: int | None = None,
        scale: int = 3,
    ) -> str:
        return await render_chart(
            "vega_lite",
            code=code,
            filename=filename,
            title=title,
            width=width,
            height=height,
            scale=scale,
        )


__all__ = ["VegaChartTool"]
