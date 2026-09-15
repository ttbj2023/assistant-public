"""知识库读图工具 - 外部工具, 按需精读知识库语料图片原图.

定位: 知识库图片的运行时读图入口, 与 analyze_image(对话附件) 互补:
语料图片不在用户附件注册表, 经 image_ref(kb_name:语料相对路径) 直接寻址.
ref 来源: tea_knowledge 等知识库检索结果中的图片条目.

路径安全: ref 必须为 kb 前缀 + 语料根内相对路径, 拒绝绝对路径与 .. 逃逸.
语料根解析: 向量库目录 kb_meta.json 记录的 corpus_root 优先,
BASE_DATA_PATH/{kb_name} 约定兜底(dev ./data, 生产 /app/data 一致).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.config.runtime_env import get_base_data_path
from src.tools.shared.tool_runtime import (
    format_tool_error,
    format_tool_success,
    sync_runnable,
)

logger = logging.getLogger(__name__)

_KB_META_FILENAME = "kb_meta.json"

_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def parse_image_ref(image_ref: str) -> tuple[str, Path]:
    """解析 image_ref 为 (kb_name, 语料根内相对路径); 非法引用抛 ValueError."""
    kb_name, sep, rel = image_ref.partition(":")
    if not sep or not kb_name.strip() or not rel.strip():
        raise ValueError(f"image_ref 格式错误(应为 kb:相对路径): {image_ref}")
    rel = rel.strip().lstrip("/")
    parts = Path(rel).parts
    if Path(rel).is_absolute() or ".." in parts:
        raise ValueError(f"image_ref 不允许绝对路径或目录逃逸: {image_ref}")
    return kb_name.strip(), Path(rel)


def resolve_corpus_root(kb_name: str) -> Path:
    """解析语料根: kb_meta.json 记录优先, BASE_DATA_PATH/{kb} 约定兜底."""
    meta_path = get_base_data_path() / "_knowledge_base" / kb_name / _KB_META_FILENAME
    root = _corpus_root_from_meta(meta_path)
    if root is not None:
        return root
    return get_base_data_path() / kb_name


def _corpus_root_from_meta(meta_path: Path) -> Path | None:
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("kb_meta.json 读取失败, 使用约定语料根: %s, %s", meta_path, e)
        return None
    root = Path(str(data.get("corpus_root", "")))
    return root if root.is_dir() else None


class KbReadImageInput(BaseModel):
    """知识库读图输入."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    image_ref: str = Field(
        description=(
            "知识库图片引用, 格式 kb:语料相对路径, "
            "如 'tea:中国茶经/images/page0415_028.jpg'. "
            "来自知识库检索结果中的图片条目, 原样传入."
        ),
    )
    prompt: str = Field(
        min_length=1,
        max_length=4000,
        description=(
            "读图要求, 必填. 明确说明想从图片获取什么信息, "
            "如识别文字/读取表格数值/描述结构细节等"
        ),
    )
    max_chars: int = Field(
        default=4000,
        ge=200,
        le=12000,
        description="返回结果最大字符数",
    )


@sync_runnable
class KbReadImageTool(BaseTool):
    """知识库读图工具 - 按需精读语料图片原图."""

    name: str = "kb_read_image"
    summary: str = "按需求精读知识库语料图片原图(书籍图版/结构图等)"
    search_keywords: ClassVar[list[str]] = [
        "知识库图片",
        "图版",
        "读图",
        "图片细节",
        "结构图",
    ]
    description: str = (
        "按需求精读知识库语料图片原图, 支持 OCR/表格数值/结构细节等定向分析.\n"
        "知识库检索结果中出现 '图片: kb:...' 条目时, 将该 image_ref 原样传入本工具, "
        "并明确说明读图要求(prompt).\n"
        "与 analyze_image(读对话上传图片) 互补, 本工具只读知识库语料图片.\n\n"
        '示例: {"image_ref": "tea:中国茶经/images/page0415_028.jpg", '
        '"prompt": "识别图中机械结构名称"}'
    )
    args_schema: type[BaseModel] = KbReadImageInput

    kb_name: str = Field(
        default="tea",
        description="默认知识库名称(仅可用性检查用, 实际以 image_ref 前缀为准)",
    )

    @override
    async def _arun(self, image_ref: str, prompt: str, max_chars: int = 4000) -> str:
        try:
            kb_name, rel_path = self._parse_ref(image_ref)
            corpus_root = self._resolve_corpus_root(kb_name)
            image_path = corpus_root / rel_path
            if not image_path.is_file():
                return format_tool_error(
                    FileNotFoundError(f"知识库图片不存在: {image_ref}"),
                )

            mime_type = _MIME_BY_SUFFIX.get(image_path.suffix.lower(), "image/jpeg")
            result, model_id = await self._read_image(image_path, mime_type, prompt)
            if not result.strip():
                return format_tool_error(RuntimeError("视觉模型未返回有效结果"))

            return format_tool_success(
                {
                    "image_ref": image_ref,
                    "model_id": model_id,
                    "prompt": prompt,
                    "result": result[:max_chars],
                },
                message="知识库图片读取完成",
            )
        except Exception as e:
            logger.exception("kb_read_image 执行失败: %s", e)
            return format_tool_error(e)

    async def is_available(self) -> bool:
        """默认知识库语料目录存在且含图片时可用; 廉价检查不加载模型."""
        root = self._resolve_corpus_root(self.kb_name)
        if not root.is_dir():
            return False
        return any(p.suffix.lower() in _MIME_BY_SUFFIX for p in root.rglob("*"))

    @staticmethod
    def _parse_ref(image_ref: str) -> tuple[str, Path]:
        """解析 image_ref, 委托模块级 parse_image_ref."""
        return parse_image_ref(image_ref)

    def _resolve_corpus_root(self, kb_name: str) -> Path:
        """解析语料根, 委托模块级 resolve_corpus_root."""
        return resolve_corpus_root(kb_name)

    async def _read_image(
        self,
        image_path: Path,
        mime_type: str,
        prompt: str,
    ) -> tuple[str, str]:
        """读取图片并调用视觉模型, 返回 (描述文本, 模型ID)."""
        from src.config.inference_config import get_config as get_inference_config

        cfg = get_inference_config().image_description
        image_bytes = await asyncio.to_thread(image_path.read_bytes)
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        model = cfg.read_image_model or cfg.model
        params = cfg.read_image_model_params or cfg.model_params
        result = await self._call_vision_model(
            model, image_base64, mime_type, prompt, params
        )
        return result, model

    async def _call_vision_model(
        self,
        model_id: str,
        image_base64: str,
        mime_type: str,
        prompt: str,
        params: dict[str, Any] | None,
    ) -> str:
        try:
            from langchain_core.messages import HumanMessage

            from src.inference.llm.model_loader import invoke_with_fallback

            message = HumanMessage(
                content=[
                    {"type": "text", "text": self._build_prompt(prompt)},
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
            from src.inference.llm.response_utils import content_to_text

            return content_to_text(response.content).strip()
        except Exception as e:
            logger.warning("视觉模型 %s 知识库读图失败: %s", model_id, e)
            return ""

    @staticmethod
    def _build_prompt(prompt: str) -> str:
        return (
            "你是知识库图片识别工具. 请只根据图片内容回答用户的读图要求, "
            "不要编造图片中不存在的信息. 如果看不清, 明确说明不确定.\n\n"
            f"用户读图要求:\n{prompt}"
        )


__all__ = ["KbReadImageInput", "KbReadImageTool"]
