"""图表渲染服务层 - 编排 tool-runtime 调用/输入存储/输出注册.

核心流程:
1. 调用 tool-runtime /render/chart 端点 (内部构建 HTML + Playwright 渲染)
2. 存储原始输入 JSON (方便后续读取修改)
3. register_tool_output() 注册输出 (签名URL + 附件 + exported_files)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from src.files.paths import FILES_CHARTS
from src.tools.shared.browser_renderer import get_browser_renderer
from src.tools.shared.file_output import (
    build_unique_filename,
    register_tool_output,
    validate_filename,
)

logger = logging.getLogger(__name__)


async def run_chart_maker(
    engine: str,
    code: str,
    filename: str,
    title: str | None,
    *,
    user_id: str,
    thread_id: str,
    width: int | None = None,
    height: int | None = None,
    scale: int = 3,
) -> dict[str, Any]:
    """执行图表渲染: 源码 → PNG + 原始输入JSON.

    Args:
        engine: 渲染引擎 ("mermaid" / "vega_lite" / "markmap")
        code: 图表源码 (mermaid语法, Vega-Lite JSON 或 markmap Markdown)
        filename: 输出文件名 (不含扩展名)
        title: 图表标题 (可选)
        user_id: 用户ID
        thread_id: 会话ID
        width: 图表逻辑宽度px (可选, vega_lite/markmap生效)
        height: 图表逻辑高度px (可选, vega_lite/markmap生效)
        scale: deviceScaleRatio清晰度倍率 (默认3高清)

    Returns:
        包含 file_id, file_url, filename, format, size_bytes 的结果字典.
    """
    try:
        validate_filename(filename)

        from src.core.path_resolver import get_user_path_resolver

        resolver = get_user_path_resolver()
        export_dir = resolver.get_shared_storage_path(
            user_id,
            thread_id,
            FILES_CHARTS,
        )
        export_dir.mkdir(parents=True, exist_ok=True)

        output_filename, display_filename = build_unique_filename(filename, "png")
        png_path = export_dir / output_filename

        # tool-runtime 完成构建 HTML + Playwright 渲染
        renderer = get_browser_renderer()
        await renderer.render_chart(
            engine=engine,
            code=code,
            title=title,
            width=width,
            height=height,
            scale=scale,
            output_path=png_path,
        )

        if not png_path.exists():
            raise RuntimeError("渲染失败, 输出文件未生成")

        # 渲染输入 spec (作为 .desc.md 源码内容, 供 read_file 回顾)
        spec = {
            "version": "1.0",
            "engine": engine,
            "code": code,
            "title": title,
            "width": width,
            "height": height,
            "scale": scale,
            "created_at": datetime.now().isoformat(),
        }

        # 注册输出 (签名URL + 附件注册 + exported_files + 配额)
        result = await register_tool_output(
            output_path=png_path,
            display_filename=display_filename,
            output_filename=output_filename,
            output_format="png",
            file_type="image",
            content=f"{engine}图表: {title or filename}",
            summary=None,
            user_id=user_id,
            thread_id=thread_id,
            brief=title,
        )
        # 源码即描述: spec 写入 .desc.md
        from src.files.desc_writer import write_desc

        write_desc(
            user_id, result["file_id"], json.dumps(spec, ensure_ascii=False, indent=2)
        )
        result["engine"] = engine
        return result

    except Exception as e:
        logger.exception("run_chart_maker 失败: %s", e)
        return {
            "success": False,
            "error": str(e),
            "message": f"图表渲染失败: {e}",
        }


__all__ = ["run_chart_maker"]
