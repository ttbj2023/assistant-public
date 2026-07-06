"""图表渲染子工具共享基类.

3个子工具 (mermaid_chart/vega_chart/markmap_chart) 共享渲染编排逻辑,
各自通过 engine ClassVar 固定引擎, 委托 service.run_chart_maker.
"""

from __future__ import annotations

import json
import logging
import re
from typing import ClassVar

from src.tools.shared.base_external_tool import BaseExternalTool

logger = logging.getLogger(__name__)

_CHART_FILENAME_MAX = 50


def _resolve_filename(engine: str, filename: str | None, title: str | None) -> str:
    """filename 为空时从 title 自动生成, title 也为空时使用 engine 相关默认值.

    生成的文件名只含 [\\w\\-.] (\\w 含中文), 保证通过 service.validate_filename
    的二次校验 (恒等). 显式提供的 filename 原样返回 (交由 service 校验).
    """
    if filename and filename.strip():
        return filename.strip()
    if title and title.strip():
        raw = title.strip()
        safe = re.sub(r"[^\w\-.]", "_", raw)[:_CHART_FILENAME_MAX].strip("_.-")
        if safe:
            return safe
    return f"{engine}_chart"


class ChartMakerBase(BaseExternalTool):
    """图表渲染子工具基类.

    子类需设置:
    - engine: ClassVar[str] 固定渲染引擎 ("mermaid"/"vega_lite"/"markmap")
    - name, summary, description, args_schema
    - _arun() 转发到 _render()

    共享 _render() 封装 context 获取 + run_chart_maker 调用 + 错误格式化.
    """

    engine: ClassVar[str] = ""
    timeout: float = 60.0

    async def _render(
        self,
        code: str,
        filename: str | None = None,
        title: str | None = None,
        width: int | None = None,
        height: int | None = None,
        scale: int = 3,
    ) -> str:
        """共享渲染逻辑: 解析文件名 → 获取用户上下文 → 调用 run_chart_maker.

        mermaid 子工具不传 width/height (默认 None, service 层忽略).
        """
        try:
            from src.core.context import get_user_context

            from .service import run_chart_maker

            resolved_filename = _resolve_filename(self.engine, filename, title)
            ctx = get_user_context()
            result = await run_chart_maker(
                engine=self.engine,
                code=code,
                filename=resolved_filename,
                title=title,
                user_id=ctx.user_id,
                thread_id=ctx.thread_id,
                width=width,
                height=height,
                scale=scale,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.exception("图表渲染失败: %s", e)
            return self._format_error(e)


__all__ = ["ChartMakerBase", "_resolve_filename"]
