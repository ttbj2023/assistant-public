"""markmap 思维导图渲染子工具 (chart_maker 技能关联工具).

固定 engine=markmap, 渲染 Markdown 层级结构为思维导图 PNG.
"""

from __future__ import annotations

from typing import ClassVar, override

from .chart_maker_base import ChartMakerBase
from .models import MarkmapChartInput


class MarkmapChartTool(ChartMakerBase):
    """渲染 markmap 思维导图为 PNG 图片."""

    engine: ClassVar[str] = "markmap"
    name: str = "markmap_chart"
    summary: str = "渲染markmap思维导图(Markdown层级结构)为PNG图片"
    description: str = (
        "渲染 markmap 思维导图为 PNG 图片.\n"
        "将 Markdown 层级结构 (标题#/列表-) 转换为树状思维导图.\n"
        "code 必须是 Markdown 源码 (如 '# 标题\\n## 子项\\n- 内容'), "
        "不能是自然语言描述.\n"
        "width/height 覆盖 SVG 画布尺寸 (px, 默认 1200x800).\n"
        "scale 控制清晰度 (1标准/3默认高清/6最大)."
    )
    args_schema: type = MarkmapChartInput

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
        return await self._render(
            code=code,
            filename=filename,
            title=title,
            width=width,
            height=height,
            scale=scale,
        )


__all__ = ["MarkmapChartTool"]
