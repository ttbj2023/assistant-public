"""OpenClaw 长消息拆分与补发.

微信渠道通过 OpenClaw 发送消息时有字符长度限制 (~4000字符).
本模块在应用层主动拆分超长响应, 第一段随 HTTP 响应返回,
后续段通过 OpenClaw Gateway HTTP API (OpenClawClient) 异步补发.

拆分原则 (按优先级):
- P0 硬约束: 每段尽量接近 limit 字符 (在 [limit × 0.7, limit] 窗口内切割)
- P1 软约束: 不在句子中间断开, 优先级 段落分隔 > 句末标点 > 单行换行 > 硬截断
- 原子保护 (atomic): 代码块/表格/数学块必须作为整体, 宁可单拆也不允许拆开
  - 超大 atomic (> limit): 自动补齐 fence/表头, 拆为多个同类单元
"""

from __future__ import annotations

import asyncio
import logging
import re

logger = logging.getLogger(__name__)

OPENCLAW_MESSAGE_CHAR_LIMIT = 2000
_WINDOW_RATIO = 0.7
_SPLIT_DELAY_SECONDS = 0.5

_TABLE_ROW_RE = re.compile(r"^\|.*\|$")
_CODE_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_MATH_FENCE_RE = re.compile(r"^\s*\$\$\s*$")

_CN_SENTENCE_END = ("。", "！", "？")  # noqa: RUF001
_EN_SENTENCE_END = (". ", "! ", "? ")

_ATOMIC_CODE = "code"
_ATOMIC_TABLE = "table"
_ATOMIC_MATH = "math"


def split_message(text: str, limit: int = OPENCLAW_MESSAGE_CHAR_LIMIT) -> list[str]:
    """将超长文本拆分为多段, 每段接近 limit 字符.

    Args:
        text: 待拆分文本
        limit: 每段最大字符数

    Returns:
        拆分后的文本段列表 (仅一段时不做标记)
    """
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    remaining = text

    while len(remaining) > limit:
        atomic_blocks = _find_atomic_blocks(remaining)

        oversize = _find_oversize_atomic(atomic_blocks, limit)
        if oversize:
            start, end, kind = oversize
            before = remaining[:start].rstrip("\n")
            if before:
                parts.append(before)
            parts.extend(_split_oversize_atomic(remaining, start, end, kind, limit))
            remaining = remaining[end:].lstrip("\n")
            if not remaining:
                break
            continue

        cut_pos = _find_best_cut(remaining, limit, atomic_blocks)
        part = remaining[:cut_pos].rstrip("\n")
        if part:
            parts.append(part)
        remaining = remaining[cut_pos:].lstrip("\n")
        if not remaining:
            break

    if remaining:
        parts.append(remaining)

    if len(parts) <= 1:
        return parts if parts else [text]

    total = len(parts)
    tagged: list[str] = []
    for i, part in enumerate(parts):
        idx = i + 1
        if idx == 1:
            tagged.append(f"{part}\n\n[{idx}/{total}]")
        else:
            tagged.append(f"[{idx}/{total}]\n{part}")
    return tagged


def _find_atomic_blocks(text: str) -> list[tuple[int, int, str]]:
    """识别必须作为整体的 markdown 元素 (代码块/表格/数学块).

    Returns:
        [(start_offset, end_offset, kind), ...] 字符偏移量 + 类型
    """
    lines = text.split("\n")
    blocks: list[tuple[int, int, str]] = []
    offset = 0

    i = 0
    while i < len(lines):
        line = lines[i]
        line_start = offset
        line_end = offset + len(line)

        fence_match = _CODE_FENCE_RE.match(line)
        if fence_match:
            fence_marker = fence_match.group(1)
            block_start = line_start
            block_end = line_end
            i += 1
            offset = line_end + 1
            while i < len(lines):
                inner_line = lines[i]
                inner_line_end = offset + len(inner_line)
                if inner_line.strip().startswith(fence_marker):
                    block_end = inner_line_end
                    i += 1
                    offset = inner_line_end + 1
                    break
                block_end = inner_line_end
                i += 1
                offset = inner_line_end + 1
            blocks.append((block_start, block_end, _ATOMIC_CODE))
            continue

        if _MATH_FENCE_RE.match(line):
            block_start = line_start
            block_end = line_end
            i += 1
            offset = line_end + 1
            while i < len(lines):
                inner_line = lines[i]
                inner_line_end = offset + len(inner_line)
                if _MATH_FENCE_RE.match(inner_line):
                    block_end = inner_line_end
                    i += 1
                    offset = inner_line_end + 1
                    break
                block_end = inner_line_end
                i += 1
                offset = inner_line_end + 1
            blocks.append((block_start, block_end, _ATOMIC_MATH))
            continue

        if _TABLE_ROW_RE.match(line.strip()):
            block_start = line_start
            block_end = line_end
            i += 1
            offset = line_end + 1
            while i < len(lines):
                inner_line = lines[i]
                if not _TABLE_ROW_RE.match(inner_line.strip()):
                    break
                inner_line_end = offset + len(inner_line)
                block_end = inner_line_end
                i += 1
                offset = inner_line_end + 1
            blocks.append((block_start, block_end, _ATOMIC_TABLE))
            continue

        i += 1
        offset = line_end + 1

    return blocks


def _find_oversize_atomic(
    atomic_blocks: list[tuple[int, int, str]],
    limit: int,
) -> tuple[int, int, str] | None:
    """返回第一个长度超过 limit 的 atomic block."""
    for start, end, kind in atomic_blocks:
        if end - start > limit:
            return (start, end, kind)
    return None


def _split_oversize_atomic(
    text: str,
    start: int,
    end: int,
    kind: str,
    limit: int,
) -> list[str]:
    """超大 atomic 拆为多段, 每段补齐格式后可独立渲染.

    Args:
        text: 完整原文
        start/end: atomic 在 text 中的偏移
        kind: atomic 类型
        limit: 每段最大字符数

    Returns:
        拆分后的段落列表 (已含补齐的 fence/表头)
    """
    content = text[start:end]
    if kind == _ATOMIC_CODE:
        return _split_oversize_code(content, limit)
    if kind == _ATOMIC_TABLE:
        return _split_oversize_table(content, limit)
    if kind == _ATOMIC_MATH:
        return _split_oversize_math(content, limit)
    return [content]


def _split_oversize_code(content: str, limit: int) -> list[str]:
    """超大代码块按行拆分, 每段加开/闭 fence (保留语言标记).

    输入 content 形如 '```python\\nline1\\nline2\\n```'.
    """
    lines = content.split("\n")
    if len(lines) < 2:
        return [content]

    open_fence = lines[0]
    fence_len = len(open_fence)
    has_close = len(lines) >= 2 and lines[-1].strip().startswith(("```", "~~~"))
    inner_lines = lines[1:-1] if has_close else lines[1:]

    reserved = fence_len + 1 + 4
    budget = max(64, limit - reserved)

    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for line in inner_lines:
        line_cost = len(line) + 1
        if current and current_len + line_cost > budget:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(line)
        current_len += line_cost
    if current:
        chunks.append(current)

    return [f"{open_fence}\n" + "\n".join(chunk).rstrip() + "\n```" for chunk in chunks]


def _split_oversize_table(content: str, limit: int) -> list[str]:
    """超大表格按行拆分, 每段补上表头 (前两行: 列名 + 分隔行).

    输入 content 形如 '| 列1 | 列2 |\\n|---|---|\\n| 数据1 | ... |'.
    非标准表格 (无 |---| 分隔行) 仍按前两行作为表头, 由调用方输入负责.
    """
    lines = content.split("\n")
    if len(lines) < 3:
        return [content]

    header = lines[:2]
    header_text = "\n".join(header)
    reserved = len(header_text) + 2
    budget = max(64, limit - reserved)

    rows = lines[2:]
    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for row in rows:
        row_cost = len(row) + 1
        if current and current_len + row_cost > budget:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(row)
        current_len += row_cost
    if current:
        chunks.append(current)

    return [header_text + "\n" + "\n".join(chunk).rstrip() for chunk in chunks]


def _split_oversize_math(content: str, limit: int) -> list[str]:
    """超大数学块按行拆分, 每段加 $$ fence."""
    lines = content.split("\n")

    has_open = bool(_MATH_FENCE_RE.match(lines[0])) if lines else False
    has_close = len(lines) >= 2 and bool(_MATH_FENCE_RE.match(lines[-1]))
    if has_open and has_close:
        inner_lines = lines[1:-1]
    elif has_open:
        inner_lines = lines[1:]
    else:
        inner_lines = lines

    reserved = 8
    budget = max(64, limit - reserved)

    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for line in inner_lines:
        line_cost = len(line) + 1
        if current and current_len + line_cost > budget:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(line)
        current_len += line_cost
    if current:
        chunks.append(current)

    if not chunks:
        return [content]

    return ["$$\n" + "\n".join(chunk).rstrip() + "\n$$" for chunk in chunks]


def _in_protected(pos: int, atomic_blocks: list[tuple[int, int, str]]) -> bool:
    """位置是否落在某个 atomic block 内部."""
    return any(s <= pos < e for s, e, _ in atomic_blocks)


def _find_best_cut(
    text: str,
    limit: int,
    atomic_blocks: list[tuple[int, int, str]],
) -> int:
    """在 [limit × 0.7, limit] 窗口内搜索最佳断点.

    优先级:
    1. 窗口内常规候选 (避开 atomic 内部): 段落 > 句末 > 单行
    2. 窗口被 atomic 覆盖时:
       - atomic 在窗口内开始 -> 在 atomic 之前切 (整段下移)
       - atomic 在窗口之前开始 -> atomic 整体单独成段 (cut = atomic.end)
    3. 兜底硬截断 limit
    """
    window_start = int(limit * _WINDOW_RATIO)

    cut = _find_last_separator(text, "\n\n", window_start, limit, atomic_blocks)
    if cut is not None:
        return cut

    cut = _find_last_sentence_end(text, window_start, limit, atomic_blocks)
    if cut is not None:
        return cut

    cut = _find_last_separator(text, "\n", window_start, limit, atomic_blocks)
    if cut is not None:
        return cut

    for start, end, _kind in atomic_blocks:
        if start < limit <= end:
            if start >= window_start and start > 0:
                return start
            return end

    return limit


def _find_last_separator(
    text: str,
    sep: str,
    window_start: int,
    limit: int,
    atomic_blocks: list[tuple[int, int, str]],
) -> int | None:
    """在 (window_start, limit] 窗口内从后往前找 sep, 跳过 atomic 内部.

    返回 sep 末尾的偏移 (即下一段的开始位置).
    """
    search_end = limit
    while True:
        pos = text.rfind(sep, window_start, search_end)
        if pos < 0:
            return None
        cut = pos + len(sep)
        if not _in_protected(pos, atomic_blocks):
            return cut
        search_end = pos


def _find_last_sentence_end(
    text: str,
    window_start: int,
    limit: int,
    atomic_blocks: list[tuple[int, int, str]],
) -> int | None:
    """在 (window_start, limit] 窗口内从后往前找句末标点.

    中文 .!? 直接匹配; 英文 . ! ? 后必须跟空格.
    返回所有候选中最靠后的位置 (跨多个分隔符取最大).
    """
    best: int | None = None

    for seps in (_CN_SENTENCE_END, _EN_SENTENCE_END):
        for sep in seps:
            search_end = limit
            while True:
                pos = text.rfind(sep, window_start, search_end)
                if pos < 0:
                    break
                cut = pos + len(sep)
                if not _in_protected(pos, atomic_blocks):
                    if best is None or cut > best:
                        best = cut
                    break
                search_end = pos

    return best


async def send_openclaw_followup(
    user_id: str,
    thread_id: str,
    agent_id: str,
    parts: list[str],
) -> None:
    """Fire-and-forget: 通过 OpenClaw Gateway 补发后续消息段.

    通过 OpenClawClient (POST /tools/invoke) 发送, 无需容器内安装 openclaw CLI.

    Args:
        user_id: 用户ID, 用于查询渠道配置
        thread_id: 线程ID
        agent_id: Agent ID, 与 thread_id 一起定位 agent 级配置
        parts: 除第一段外的后续消息段列表
    """
    if not parts:
        return

    # 等待 OpenClaw 被动通道把 HTTP 响应中的 parts[0] 投递完毕, 避免与主动
    # invoke 推送产生乱序 (被动通道走 reply-dispatcher, 含 chunked send +
    # 网络 RTT, 实测 500ms-1.5s). 取 3s 留充分余量, 且用户读完首段 ~2000
    # 字耗时更长, 不影响体验.
    await asyncio.sleep(3.0)

    from src.core.notification import get_notification_service, resolve_delivery

    delivery = await resolve_delivery(user_id, thread_id, agent_id, "wechat")
    if delivery is None:
        logger.warning(
            "OpenClaw补发跳过: 用户%s微信渠道配置缺失或不完整",
            user_id,
        )
        return

    notifier = get_notification_service()

    total = len(parts)
    for i, part in enumerate(parts):
        if i > 0:
            await asyncio.sleep(_SPLIT_DELAY_SECONDS)
        # 微信渲染缺陷: 末尾 `> **...**` 引用块行会被吞掉
        # 追加零宽空格占位行, 不可见且不影响其他渠道
        text_to_send = f"{part}\n\u200b"
        success = await notifier.send(delivery, text_to_send)
        idx = i + 2
        if success:
            logger.info(
                "✅ OpenClaw补发成功: 用户%s 第%d/%d段",
                user_id,
                idx,
                total + 1,
            )
        else:
            logger.warning(
                "❌ OpenClaw补发失败: 用户%s 第%d/%d段",
                user_id,
                idx,
                total + 1,
            )
