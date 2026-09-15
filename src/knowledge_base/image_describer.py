"""知识库图片离线描述器 - vision 题注 + sidecar 幂等缓存.

为索引层提供图片题注: 每张语料图片经视觉模型生成一段事实性中文题注
(图类型/主体/关键可见信息), 结果连同图片 content hash 落 sidecar
(图片同目录 .desc/<文件名>.json). 未变更图片(hash 一致)二次构建直接命中缓存.

题注用纯文本输出(非JSON): 复用上传链路的 JSON 双层 prompt 时, 视觉模型
输出不稳会产生 JSON 碎片被降级缓存, 纯文本 prompt + 碎片防御更稳.

失败策略: 图片缺失/视觉失败/碎片结果(以{开头/过短)返回 None 且不写 sidecar,
保证坏结果不被缓存, 索引层据此跳过该图片.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SIDECAR_DIRNAME = ".desc"

_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

_CAPTION_PROMPT = (
    "你是书籍图版编目员. 请根据图片内容生成一段事实性中文题注: "
    "说明图片类型(照片/线条图/表格等)、主体内容、可见的关键信息"
    "(设备名/文字/数据/结构特征). 只描述确定可见的内容, 不推测; "
    "看不清的部分明确说'部分不清晰'. 150字以内纯文本, 不要JSON, 不要标题前缀."
)

# 题注有效性防御阈值
_MIN_CAPTION_CHARS = 8


class KnowledgeImageDescriber:
    """知识库图片描述器 - 题注式纯文本生成 + sidecar 缓存."""

    async def describe(self, corpus_root: Path, rel_path: str) -> str | None:
        """生成(或从缓存读取)图片题注.

        Args:
            corpus_root: 语料根目录
            rel_path: 图片相对语料根路径(posix 风格)

        Returns:
            题注文本; 图片缺失或描述失败返回 None

        """
        image_path = corpus_root / rel_path
        if not image_path.is_file():
            logger.warning("知识库图片不存在, 跳过描述: %s", rel_path)
            return None

        image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
        sidecar = self._sidecar_path(corpus_root, rel_path)

        cached = self._load_cache(sidecar)
        if cached and cached.get("hash") == image_hash:
            return str(cached.get("caption", ""))

        caption = await self._describe_via_vision(
            image_path,
            _MIME_BY_SUFFIX.get(image_path.suffix.lower(), "image/jpeg"),
        )
        if caption is None or not self._is_valid_caption(caption):
            logger.warning("图片题注无效, 不写缓存: %s", rel_path)
            return None

        self._save_cache(sidecar, image_hash, caption)
        logger.info("知识库图片题注已生成: %s", rel_path)
        return caption

    async def _describe_via_vision(
        self, image_path: Path, mime_type: str
    ) -> str | None:
        """调用视觉模型生成题注; 失败返回 None."""
        try:
            from langchain_core.messages import HumanMessage

            from src.config.inference_config import get_config as get_inference_config
            from src.inference.llm.model_loader import invoke_with_fallback
            from src.inference.llm.response_utils import content_to_text

            cfg = get_inference_config().image_description
            image_bytes = await asyncio.to_thread(image_path.read_bytes)
            image_base64 = base64.b64encode(image_bytes).decode("utf-8")

            message = HumanMessage(
                content=[
                    {"type": "text", "text": _CAPTION_PROMPT},
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
                cfg.model,
                cfg.model_params,
                fallback_kind="vision",
                usage_tag="vision_description",
                use_json_mode=False,
            )
            return content_to_text(response.content).strip()
        except Exception as e:
            logger.warning("视觉模型题注生成失败(%s): %s", image_path.name, e)
            return None

    @staticmethod
    def _is_valid_caption(caption: str) -> bool:
        """题注有效性: 非JSON碎片(不以{开头)且不过短."""
        stripped = caption.strip()
        if stripped.startswith("{") or stripped.startswith("```"):
            return False
        return len(stripped) >= _MIN_CAPTION_CHARS

    @staticmethod
    def _sidecar_path(corpus_root: Path, rel_path: str) -> Path:
        """sidecar 紧邻图片: <图片目录>/.desc/<文件名>.json."""
        rel = Path(rel_path)
        return corpus_root / rel.parent / _SIDECAR_DIRNAME / f"{rel.name}.json"

    @staticmethod
    def _load_cache(sidecar: Path) -> dict[str, Any] | None:
        if not sidecar.is_file():
            return None
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("图片描述缓存损坏, 忽略: %s, %s", sidecar, e)
            return None

    @staticmethod
    def _save_cache(sidecar: Path, image_hash: str, caption: str) -> None:
        try:
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(
                json.dumps(
                    {"hash": image_hash, "caption": caption},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("图片描述缓存写入失败: %s, %s", sidecar, e)


__all__ = ["KnowledgeImageDescriber"]
