"""代码文件摘要生成器 - 调用文本模型生成代码文件的 brief 和结构化摘要.

与 ImageDescriber 同构的三段式: 失败策略 (空结果, 不抛异常) /
模型调用 (invoke_with_fallback) / 响应解析 (JSON 优先, 纯文本兜底).

desc 语义: AI 摘要 + 代码原文 (统一结构), 摘要由本模块生成,
原文由调用方 (background_generate_code_summary) 持有.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.inference.llm.response_utils import content_to_text

logger = logging.getLogger(__name__)


class CodeDescriber:
    """代码文件摘要生成器.

    调用文本模型生成两层摘要:
    - brief: 一句话概要 (不超过 30 字)
    - summary: 结构化摘要 (语言/功能/结构/关键函数清单)

    无状态, 线程安全.
    """

    CODE_SUMMARY_PROMPT = """请分析以下代码文件, 提供两个层次的摘要:

文件名: {filename}

1. brief: 一句话概要(不超过30字), 说明文件用途
2. summary: 结构化摘要, 包含:
   - 语言: 编程语言
   - 功能: 核心功能描述
   - 结构: 模块组织 (类/函数/分区的简明清单, 每项一行)

代码内容:
```
{code}
```

请返回JSON格式:
{{
    "brief": "简短概要(不超过30字)",
    "summary": "语言: ...\\n功能: ...\\n结构: ..."
}}"""

    async def describe(self, filename: str, content: str) -> tuple[str, str]:
        """生成代码文件摘要 (brief + summary).

        Args:
            filename: 文件名 (用于语言上下文提示)
            content: 代码原文

        Returns:
            (brief, summary) 元组, 失败或空结果返回 ("", "")

        """
        try:
            from src.config.inference_config import (
                get_config as get_inference_config,
            )

            config = get_inference_config().code_description
            truncated = content[: config.max_input_chars]
            if len(content) > config.max_input_chars:
                truncated += "\n... (代码过长已截断)"

            brief, summary = await self._call_model(
                config.model,
                self.CODE_SUMMARY_PROMPT.format(filename=filename, code=truncated),
                config.model_params,
            )
            if brief:
                logger.info("📐 代码摘要生成成功(%s): %s", config.model, brief)
                return brief, summary

            logger.warning("⚠️ 代码摘要生成空结果")
            return "", ""

        except Exception as e:
            logger.warning("⚠️ 代码摘要生成失败: %s", e)
            return "", ""

    async def _call_model(
        self,
        model_id: str,
        prompt: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """调用文本模型生成摘要.

        Returns:
            (brief, summary) 元组, 失败返回 ("", "")

        """
        try:
            from langchain_core.messages import HumanMessage

            from src.inference.llm.model_loader import invoke_with_fallback

            response = await invoke_with_fallback(
                [HumanMessage(content=prompt)],
                model_id,
                params,
                fallback_kind="text",
                usage_tag="code_description",
                use_json_mode=False,
            )
            return self._extract_summary_from_response(response.content)

        except Exception as e:
            logger.warning("⚠️ 文本模型 %s 调用失败: %s", model_id, e)
            return "", ""

    def _extract_summary_from_response(
        self,
        response_content: Any,
    ) -> tuple[str, str]:
        """从模型响应中提取 brief 和 summary.

        Returns:
            (brief, summary) 元组

        """
        response_content = content_to_text(response_content)

        if not isinstance(response_content, str) or not response_content.strip():
            return "", ""

        try:
            json_match = re.search(
                r'\{[^{}]*"brief"[^{}]*\}',
                response_content,
                re.DOTALL,
            )
            if not json_match:
                json_match = re.search(r"\{.*?\}", response_content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                brief = str(data.get("brief", "")).strip()
                summary = str(data.get("summary", "")).strip()
                if brief or summary:
                    return brief, summary
        except (json.JSONDecodeError, AttributeError):
            pass

        # 纯文本兜底: 首行作 brief, 全文作 summary
        cleaned = response_content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```\w*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
            cleaned = cleaned.strip()

        if cleaned:
            return cleaned.splitlines()[0][:50], cleaned
        return "", ""


__all__ = ["CodeDescriber"]
