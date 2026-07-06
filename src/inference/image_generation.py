"""图片生成服务 - 封装火山引擎 Ark Images API."""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from src.core.http_pool import get_http_pool
from src.inference.llm.definitions import get_provider_config
from src.inference.llm.definitions.model_registry import get_model
from src.inference.llm.definitions.model_types import ModelCapability
from src.inference.llm.definitions.provider_registry import require_api_key_env
from src.inference.usage import record_usage_from_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeneratedImage:
    """图片生成结果."""

    image_data: bytes
    mime_type: str
    revised_prompt: str | None = None


class ImageGenerationService:
    """图片生成服务."""

    async def generate_image(
        self,
        *,
        model_id: str,
        prompt: str,
        size: str,
        guidance_scale: float | None = None,
        seed: int | None = None,
        watermark: bool | None = None,
        timeout: float = 120.0,
    ) -> GeneratedImage:
        """调用图片生成模型并返回图片二进制."""
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("图片生成提示词不能为空")

        metadata = get_model(model_id)
        if not metadata:
            raise ValueError(f"未知图片生成模型: {model_id}")
        if not metadata.has_capability(ModelCapability.IMAGE_GENERATION):
            raise ValueError(f"模型不支持图片生成: {model_id}")

        provider_config = get_provider_config(metadata.provider)
        api_key = self._get_api_key(provider_config.api_key_env)
        base_url = provider_config.get_effective_base_url() or (
            "https://ark.cn-beijing.volces.com/api/v3"
        )

        payload: dict[str, Any] = {
            "model": metadata.id.split(":", 1)[1],
            "prompt": prompt,
            "size": size,
            "response_format": "b64_json",
        }
        if guidance_scale is not None:
            payload["guidance_scale"] = guidance_scale
        if seed is not None:
            payload["seed"] = seed
        if watermark is not None:
            payload["watermark"] = watermark

        client = get_http_pool().get(metadata.provider)
        started_at = time.time()
        raw_result: dict | None = None
        success = False
        try:
            response = await client.post(
                self._build_url(base_url),
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()

            result = response.json()
            raw_result = result if isinstance(result, dict) else None
            data = result.get("data") or []
            if not data:
                raise RuntimeError("图片生成接口未返回图片数据")

            first = data[0]
            revised_prompt = first.get("revised_prompt")

            b64_json = first.get("b64_json")
            if b64_json:
                success = True
                return GeneratedImage(
                    image_data=base64.b64decode(b64_json),
                    mime_type="image/png",
                    revised_prompt=revised_prompt,
                )

            image_url = first.get("url")
            if image_url:
                generated = await self._download_image(
                    client=client,
                    image_url=image_url,
                    timeout=timeout,
                    revised_prompt=revised_prompt,
                )
                success = True
                return generated

            raise RuntimeError("图片生成接口返回结果缺少 b64_json/url")
        finally:
            record_usage_from_context(
                operation="image_generation",
                provider=metadata.provider,
                model_id=metadata.id,
                unit_type="count",
                request_count=1,
                accuracy="exact" if success else "unknown",
                success=success,
                duration_ms=max(int((time.time() - started_at) * 1000), 0),
                raw_usage=raw_result,
                metadata={
                    "size": size,
                    "guidance_scale": guidance_scale,
                    "seed": seed,
                    "watermark": watermark,
                },
            )

    @staticmethod
    def _get_api_key(env_name: str | None) -> str:
        try:
            return require_api_key_env(env_name, purpose="图片生成模型")
        except RuntimeError as e:
            raise ValueError(str(e)) from e

    @staticmethod
    def _build_url(base_url: str) -> str:
        return f"{base_url.rstrip('/')}/images/generations"

    @staticmethod
    async def _download_image(
        *,
        client: httpx.AsyncClient,
        image_url: str,
        timeout: float,
        revised_prompt: str | None,
    ) -> GeneratedImage:
        response = await client.get(image_url, timeout=timeout)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "image/png")
        mime_type = content_type.split(";", 1)[0].strip() or "image/png"
        return GeneratedImage(
            image_data=response.content,
            mime_type=mime_type,
            revised_prompt=revised_prompt,
        )


__all__ = ["GeneratedImage", "ImageGenerationService"]
