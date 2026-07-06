"""Open WebUI 兼容的 HTML 格式化工具.

将工具调用信息格式化为 <details type="tool_calls"> 标签,
供 Open WebUI 前端解析并渲染为可折叠的工具调用组件.

格式规范参考: open-webui/docs/BACKEND_FORMATTING_GUIDE.md
"""

from __future__ import annotations

import html
import json


def _escape_attr(value: str) -> str:
    """HTML 属性值转义."""
    return html.escape(str(value), quote=True)


def format_tool_call_done(name: str, result: str, arguments: dict | None = None) -> str:
    """格式化工具调用完成标签 (done=true).

    在工具执行完毕后发送, 包含执行结果.

    结果同时放入 result 属性和 <details> body 中:
    - result 属性: 供规范兼容的前端使用
    - body 内容: Open WebUI 的 ToolCallDisplay.svelte 会优先读取 resultContent(body)

    Args:
        name: 工具名称
        result: 工具执行结果文本 (会自动截断到 MAX_RESULT_LENGTH)
        arguments: 工具参数

    Returns:
        <details type="tool_calls" done="true"> HTML 字符串

    Note:
        前置 \\n\\n 确保 <details> 在新行开始, 这是 Open WebUI 的 marked
        details 扩展匹配的前提条件 (^<details).
        detailsTokenizer 正则: /^<details(\\s+[^>]*)?>\\n/

    """
    args_json = _escape_attr(json.dumps(arguments or {}, ensure_ascii=False))
    name_escaped = _escape_attr(name)
    truncated = result[:MAX_RESULT_LENGTH]
    # result 属性: 直接 HTML 转义原始字符串
    result_attr = _escape_attr(truncated)
    # body 内容: JSON 序列化后 HTML 转义 (ToolCallDisplay 优先读取 resultContent)
    result_body = html.escape(
        json.dumps(truncated, ensure_ascii=False),
        quote=False,
    )
    return (
        f'\n\n<details type="tool_calls" name="{name_escaped}" '
        f'arguments="{args_json}" result="{result_attr}" done="true">\n'
        f"<summary>Tool Executed</summary>\n"
        f"{result_body}\n"
        f"</details>\n\n"
    )


MAX_RESULT_LENGTH = 2000


__all__ = [
    "format_tool_call_done",
]
