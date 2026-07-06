"""LLM 响应 content 归一化工具.

部分模型(尤其是 gemini 系)经原生 SDK 返回的 content 可能是 list 格式,
例如:
- Gemma 的 thinking 块: [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "..."}]
- gemini-3 的 text 块: [{"type": "text", "text": "...", "extras": {...}}]

调用方需要以一致的行为消费模型输出, 本模块提供统一的 content -> text 转换.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["content_to_text", "filter_think_tags_streaming", "strip_think_tags"]

# think / thinking 标签正则 (跨供应商防御性过滤)
# 支持的标签名: <think>/<thinking> 及其闭合形式
# 注意: 不匹配 <|think|> 等模型输入控制 token
_THINK_OPEN_RE = re.compile(r"<(think|thinking)\s*>")
_THINK_CLOSE_RE = re.compile(r"</(think|thinking)\s*>")
# 完整标签对: 开闭标签名必须一致 (通过 \1 反向引用保证)
_THINK_PAIR_RE = re.compile(r"<(think|thinking)\s*>.*?</\1\s*>", re.DOTALL)


def content_to_text(content: Any) -> str:
    """将 LLM 响应 content 归一化为纯文本字符串.

    处理规则:
    - str: 原样返回
    - list: 拼接所有文本部分
      - type==text 的 text 字段
      - 含有裸 text 键的 dict
      - 裸字符串
      跳过 thinking,tool_use 等非文本块
    - 其它: 先尝试 json.dumps 兜底, 失败则 str()

    Args:
        content: LLM 响应的 content, 可能为 str / list / 其它类型

    Returns:
        纯文本字符串
    """
    if content is None:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and (
                item.get("type") == "text" or "text" in item
            ):
                parts.append(str(item.get("text", "")))
        return "".join(parts)

    try:
        import json

        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def strip_think_tags(content: str) -> str:
    """移除 LLM 响应中的 thinking/reasoning 标签及其内容 (非流式).

    覆盖三种现实格式 (跨供应商防御):
    1. 完整标签对: <think>...</think> / <thinking>...</thinking>
    2. 孤立闭合标签 (qwen3.7-plus 多模态泄露): reasoning内容\n</think>\n正文
       此时 </think> 左侧全部视为 reasoning_content, 只有右侧是真正的正文.
    3. 纯文本无标签: 原样返回

    注意: 不处理 <|think|> 等模型输入控制 token (那是输入侧, 非输出).

    Args:
        content: LLM 响应文本

    Returns:
        过滤后的可见文本
    """
    if not content:
        return content

    # 1. 先移除完整标签对 (开闭标签名必须一致)
    content = _THINK_PAIR_RE.sub("", content)

    # 2. 若仍残留孤立闭合标签, 取最后一个 </think|thinking> 之后的内容.
    #    语义: 孤立闭合意味着其左侧全是 reasoning, 只有右侧是正文.
    parts = _THINK_CLOSE_RE.split(content)
    if len(parts) > 1:
        content = parts[-1]

    return content.lstrip("\n")


def filter_think_tags_streaming(
    content: str,
    in_think_block: bool,
    think_buffer: str,
) -> str | tuple[bool, str]:
    """流式 think/thinking 标签过滤 (跨 chunk 状态机).

    返回值契约:
    - str: 过滤后的可见文本, 调用方应 yield 出去
    - tuple[bool, str]: (in_think_block, think_buffer) 新状态, 调用方应 continue

    覆盖格式:
    - 完整标签对: <think>...</think> / <thinking>...</thinking>
    - 孤立闭合标签 (块外突然出现 </think>): 丢弃标签及其左侧当前 chunk 内容

    权衡:
    - 不做标签前缀碎片缓冲 (如 "<thi" 被切断在 chunk 边界).
      流式实测不触发泄露, 且碎片本身不含 reasoning 语义; 加缓冲会破坏
      think_buffer 语义并需改调用方契约, 复杂度收益比差.

    Args:
        content: 当前 chunk 文本 (已 content_to_text 归一化)
        in_think_block: 是否已在 think 块内
        think_buffer: 跨 chunk 累积的 think 块缓冲

    Returns:
        可见文本 或 (in_think_block, think_buffer) 状态更新
    """
    combined = think_buffer + content

    if in_think_block:
        close_match = _THINK_CLOSE_RE.search(combined)
        if close_match:
            remaining = combined[close_match.end() :]
            if not remaining:
                return (False, "")
            return filter_think_tags_streaming(remaining, False, "")
        # 仍在块内, 累积缓冲
        return (True, combined)

    # 块外: 同时查找开标签与闭合标签
    open_match = _THINK_OPEN_RE.search(combined)
    close_match = _THINK_CLOSE_RE.search(combined)

    # 孤立闭合防御: 闭合标签在开标签之前 (或根本没有开标签).
    # 这对应 qwen3.7-plus 多模态泄露: reasoning_content 被合并进 content,
    # 仅保留 </think> 分隔符, 没有 <think> 开标签.
    if close_match and (not open_match or close_match.start() < open_match.start()):
        remaining = combined[close_match.end() :]
        if not remaining:
            return ""
        result = filter_think_tags_streaming(remaining, False, "")
        # 右侧若进入块状态, 本次无可见输出; 否则输出右侧处理后文本
        return "" if isinstance(result, tuple) else result

    if not open_match:
        # 无标签, 正常输出
        return combined

    # 有开标签: 输出标签前内容, 在标签后内容中查找闭合
    before = combined[: open_match.start()]
    after_open = combined[open_match.end() :]
    close_match = _THINK_CLOSE_RE.search(after_open)
    if close_match:
        after_close = after_open[close_match.end() :]
        if after_close:
            remainder = filter_think_tags_streaming(after_close, False, "")
            remainder_text = "" if isinstance(remainder, tuple) else remainder
            full = before + remainder_text
        else:
            full = before
        return full if full else ""

    # 开标签未闭合: 先输出开标签前的正文 (如果有), 再进入 think 块状态
    if before.strip():
        return before
    return (True, after_open)
