"""索引弧短语分析器 - 把一个语义 run 压缩成一句冻结的弧短语.

职责: 输入一个已闭合 run 的各轮 (topic, summary), 蒸馏一句叙事性弧短语,
一次性生成后冻结(永不再压缩). 弧短语兼检索钩子(语义线索)与时间连续性
(按序拼接构成早期对话演变轨迹).

设计要点:
- 始终用摘要: 输入是各轮已有 summary, 不读原文(对齐"始终用摘要"决定)
- 叙事性: 概括"发生了什么/怎么演进", 非关键词堆砌(关键词袋杀死连续性)
- 冻结: 调用方负责一次性写入, 此处只负责蒸馏
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage

from src.inference.llm.model_loader import invoke_with_fallback
from src.inference.llm.response_utils import content_to_text

logger = logging.getLogger(__name__)

# prompt 字数目标比硬截断留 10 字余量(LLM 字数不精准, 留缓冲减少半句截断)
_ARC_LENGTH_MARGIN = 10

INDEX_ARC_PROMPT = """你是对话时间线归档员. 把下面一段连续同主题对话(run)压缩成一句"弧短语".

要求:
- 一句话, 不超过{max_chars}字, 语言必须与用户输入一致
- 叙事性: 概括这段对话"发生了什么/怎么演进的", 不是关键词堆砌
- 保留可区分的主题线索(日后据此判断相关性,检索早期对话)
- 这句弧短语将长期冻结, 与其他弧短语按时间顺序拼接, 构成早期对话的演变轨迹

## 本 run 对话(R{start}-R{end})
{entries}

返回JSON: {{"arc_phrase": "..."}}
"""


def format_run_entries(entries: list[dict[str, Any]]) -> str:
    """格式化 run 各轮为紧凑文本(供 LLM 蒸馏)."""
    lines = []
    for e in entries:
        topic = (e.get("topic") or "").strip()
        summary = (e.get("summary") or "").strip()
        lines.append(f"R{e.get('round', '?')}: {topic} - {summary}")
    return "\n".join(lines)


def _extract_json(content: str) -> dict:
    text = content.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"响应中未找到有效JSON: {text[:200]}")


def parse_arc_phrase(content: str, max_chars: int) -> str:
    """解析 LLM 输出为弧短语, 兜底截断到 max_chars."""
    data = _extract_json(content)
    phrase = str(data.get("arc_phrase", "")).strip()
    if not phrase:
        raise ValueError("arc_phrase 为空")
    if len(phrase) > max_chars:
        phrase = phrase[:max_chars]
    return phrase


class IndexArcAnalyzer:
    """索引弧短语分析器.

    闭合一个 run 时调用: 各轮 summary → 一句冻结弧短语.
    """

    def __init__(
        self,
        model_id: str,
        model_params: dict[str, Any] | None = None,
        max_chars: int = 60,
    ) -> None:
        self.model_id = model_id
        self.model_params = model_params or {}
        self.max_chars = max_chars

    async def distill(
        self,
        round_start: int,
        round_end: int,
        entries: list[dict[str, Any]],
    ) -> str:
        """蒸馏一个 run 的弧短语.

        Args:
            round_start: run 起始轮次
            round_end: run 结束轮次
            entries: run 各轮 [{round, topic, summary}, ...]

        Returns:
            弧短语字符串(已截断到 max_chars)

        """
        prompt = INDEX_ARC_PROMPT.format(
            max_chars=max(1, self.max_chars - _ARC_LENGTH_MARGIN),
            start=round_start,
            end=round_end,
            entries=format_run_entries(entries),
        )
        from src.inference.usage import usage_source

        with usage_source("memory_analyzer"):
            resp = await invoke_with_fallback(
                [HumanMessage(content=prompt)],
                self.model_id,
                self.model_params,
                fallback_kind="text",
                usage_tag="memory_analyzer",
                primary_json_log_level=logging.DEBUG,
            )
        phrase = parse_arc_phrase(content_to_text(resp.content), self.max_chars)
        logger.info(
            "索引弧短语蒸馏完成 R%d-R%d: %s",
            round_start,
            round_end,
            phrase,
        )
        return phrase
