"""图片视觉描述生成器 - 调用视觉模型生成图片的 brief 和 detail.

从 src/storage/service/attachment_service.py 拆分而来. 视觉描述本质是模型推理职责,
不应属于存储层. 本模块是纯推理组件, 不依赖任何存储/Service.

失败策略: 描述生成失败时返回默认描述 ("图片", ""), 不抛异常.
推理失败不应阻塞存储流程 (调用方先存文件, 后台补描述).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path
from typing import Any

from src.inference.llm.response_utils import content_to_text

logger = logging.getLogger(__name__)


class ImageDescriber:
    """图片视觉描述生成器.

    调用视觉模型 (Doubao 主模型) 生成两层描述:
    - brief: 一句话概要 (不超过 30 字)
    - detail: 完整画面描述 + 逐字转录所有可见文字

    无状态, 线程安全.
    """

    IMAGE_DESCRIPTION_PROMPT = """请分析这张图片,提供两个层次的描述:

1. brief: 一句话概要(不超过30字),说明图片类型和主题
2. detail: 严格按照以下步骤完整记录图片中的所有内容:
   - 第一步:整体画面描述(场景,布局,视觉特征等)
   - 第二步:逐字转录所有可见文字.按从上到下,从左到右的顺序,保持原始排版层级和标点符号,不要概括,改写,补全或省略任何文字.
   - 特殊要求:
     * 数学公式/符号:用 LaTeX 表示(如 $x^2+2x+1$)
     * 代码/终端内容:保持原始缩进和换行,不添加额外格式
     * 表格/列表:尽量用 Markdown 表格或列表还原原始结构
     * 手写内容:照原样转录,如果无法辨认则标注[手写:无法辨认]
   - 如果文字非常密集,优先保证文字内容的完整性,宁可多转录也不要遗漏

请返回JSON格式:
{
    "brief": "简短概要(不超过30字)",
    "detail": "画面描述 + 逐字转录的所有原始文字"
}"""

    async def describe(
        self,
        image_path: Path,
        mime_type: str = "image/jpeg",
    ) -> tuple[str, str]:
        """生成图片描述 (brief + detail).

        Args:
            image_path: 图片文件路径
            mime_type: MIME 类型

        Returns:
            (brief, detail) 元组, 失败或空结果返回 ("图片", "")

        """
        try:
            from src.config.inference_config import (
                get_config as get_inference_config,
            )

            inference_config = get_inference_config()
            model = inference_config.image_description.model
            params = inference_config.image_description.model_params

            image_data = await asyncio.to_thread(image_path.read_bytes)
            image_base64 = base64.b64encode(image_data).decode("utf-8")

            brief, detail = await self._call_vision_model(
                model,
                image_base64,
                mime_type,
                params,
            )
            if brief:
                logger.info("🎨 图片描述生成成功(%s): %s", model, brief)
                return brief, detail

            logger.warning("⚠️ 图片描述生成空结果,使用默认描述")
            return "图片", ""

        except Exception as e:
            logger.warning("⚠️ 图片描述生成失败: %s,使用默认描述", e)
            return "图片", ""

    async def _call_vision_model(
        self,
        model_id: str,
        image_base64: str,
        mime_type: str = "image/jpeg",
        params: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """调用视觉模型生成图片描述.

        Returns:
            (brief, detail) 元组, 失败返回 ("", "")

        """
        try:
            from langchain_core.messages import HumanMessage

            from src.inference.llm.model_loader import invoke_with_fallback

            message = HumanMessage(
                content=[
                    {"type": "text", "text": self.IMAGE_DESCRIPTION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_base64}",
                        },
                    },
                ],
            )

            response = await invoke_with_fallback(
                [message],
                model_id,
                params,
                fallback_kind="vision",
                usage_tag="vision_description",
                use_json_mode=False,
            )
            return self._extract_description_from_response(response.content)

        except Exception as e:
            logger.warning("⚠️ 视觉模型 %s 调用失败: %s", model_id, e)
            return "", ""

    def _extract_description_from_response(
        self,
        response_content: Any,
    ) -> tuple[str, str]:
        """从模型响应中提取 brief 和 detail.

        Returns:
            (brief, detail) 元组

        """
        response_content = content_to_text(response_content)

        if not isinstance(response_content, str):
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
                detail = str(
                    data.get("detail", data.get("description", "")),
                ).strip()
                return brief, detail
        except (json.JSONDecodeError, AttributeError):
            pass

        cleaned = response_content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```\w*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
            cleaned = cleaned.strip()

        if cleaned:
            return cleaned[:50], cleaned
        return "", ""


__all__ = ["ImageDescriber"]
