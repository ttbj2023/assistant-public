"""视频生成服务 - 封装火山引擎 Ark Contents Generations API (异步任务模式).

支持 Seedance 2.0 系列的全部输入模式:
- 文生视频: 仅文本提示词
- 图生视频-首帧: 1张首帧图片 + 可选文本
- 图生视频-首尾帧: 首帧+尾帧图片 + 可选文本
- 多模态参考生视频: 参考图片(0-9)+参考视频(0-3)+参考音频(0-3) + 可选文本
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.core.http_pool import get_http_pool
from src.inference.llm.definitions import get_provider_config
from src.inference.llm.definitions.model_registry import get_model
from src.inference.llm.definitions.model_types import ModelCapability
from src.inference.llm.definitions.provider_registry import require_api_key_env
from src.inference.usage import record_usage_from_context

logger = logging.getLogger(__name__)

_SUBMIT_PATH = "/contents/generations/tasks"
_POLL_PATH = "/contents/generations/tasks/{task_id}"
_DEFAULT_POLL_INTERVAL = 5.0
_DEFAULT_MAX_POLL_TIME = 600.0
_TERMINAL_STATUSES = {"succeeded", "failed", "expired"}


@dataclass(frozen=True)
class GeneratedVideo:
    """视频生成结果."""

    video_data: bytes
    mime_type: str
    task_id: str
    duration: int | None = None
    usage: dict[str, Any] | None = None


@dataclass
class VideoContentBlock:
    """视频生成内容块, 对应 Seedance API 的 content[] 元素."""

    type: str
    url: str | None = None
    text: str | None = None
    role: str | None = None


@dataclass
class VideoGenerationRequest:
    """视频生成请求参数.

    content_blocks 由调用方构建, 包含文本/图片/视频/音频内容块.
    其余参数为视频生成控制参数.
    """

    content_blocks: list[VideoContentBlock] = field(default_factory=list)
    ratio: str | None = None
    duration: int | None = None
    resolution: str | None = None
    generate_audio: bool | None = None
    seed: int | None = None
    watermark: bool | None = None


class VideoGenerationService:
    """视频生成服务 (异步任务模式: 提交 -> 轮询 -> 下载)."""

    async def generate_video(
        self,
        *,
        model_id: str,
        request: VideoGenerationRequest,
        timeout: float = _DEFAULT_MAX_POLL_TIME,
    ) -> GeneratedVideo:
        """调用视频生成模型并返回视频二进制.

        Args:
            model_id: 模型ID, 如 "ark-agent-plan:doubao-seedance-2.0"
            request: 视频生成请求参数(含内容块)
            timeout: 最大等待时间(秒)

        """
        metadata = get_model(model_id)
        if not metadata:
            raise ValueError(f"未知视频生成模型: {model_id}")
        if not metadata.has_capability(ModelCapability.VIDEO_GENERATION):
            raise ValueError(f"模型不支持视频生成: {model_id}")

        if not request.content_blocks:
            raise ValueError("视频生成请求缺少内容")

        provider_config = get_provider_config(metadata.provider)
        api_key = self._get_api_key(provider_config.api_key_env)
        base_url = provider_config.get_effective_base_url() or (
            "https://ark.cn-beijing.volces.com/api/v3"
        )

        payload = self._build_payload(metadata, request)

        client = get_http_pool().get(metadata.provider)

        started_at = time.time()
        task_id: str | None = None
        task_result: dict[str, Any] | None = None
        success = False
        try:
            task_id = await self._submit_task(client, base_url, api_key, payload)

            logger.info(
                "Seedance 任务已提交: task_id=%s, model=%s, content_types=%s",
                task_id,
                model_id,
                [b.type for b in request.content_blocks],
            )

            task_result = await self._poll_task(
                client,
                base_url,
                api_key,
                task_id,
                timeout,
            )

            generated = await self._download_video(client, task_result, task_id)
            success = True
            return generated
        finally:
            usage = task_result.get("usage") if isinstance(task_result, dict) else None
            self._record_video_usage(
                metadata=metadata,
                request=request,
                task_id=task_id,
                usage=usage if isinstance(usage, dict) else None,
                success=success,
                duration_ms=max(int((time.time() - started_at) * 1000), 0),
            )

    def _build_payload(
        self,
        metadata: Any,
        request: VideoGenerationRequest,
    ) -> dict[str, Any]:
        """构建 API 请求体."""
        content: list[dict[str, Any]] = []
        for block in request.content_blocks:
            item = self._serialize_content_block(block)
            content.append(item)

        payload: dict[str, Any] = {
            "model": metadata.id.split(":", 1)[1],
            "content": content,
        }

        param_defaults = metadata.get_param_defaults()
        if request.ratio is not None:
            payload["ratio"] = request.ratio
        elif "ratio" in param_defaults:
            payload["ratio"] = param_defaults["ratio"]

        if request.duration is not None:
            payload["duration"] = request.duration

        if request.resolution is not None:
            payload["resolution"] = request.resolution

        if request.generate_audio is not None:
            payload["generate_audio"] = request.generate_audio
        elif "generate_audio" in param_defaults:
            payload["generate_audio"] = param_defaults["generate_audio"]

        if request.seed is not None:
            payload["seed"] = request.seed

        if request.watermark is not None:
            payload["watermark"] = request.watermark

        return payload

    @staticmethod
    def _serialize_content_block(block: VideoContentBlock) -> dict[str, Any]:
        """将内容块序列化为 API 格式."""
        if block.type == "text":
            item: dict[str, Any] = {"type": "text", "text": block.text or ""}
            return item

        if block.type == "image_url":
            item = {"type": "image_url", "image_url": {"url": block.url or ""}}
        elif block.type == "video_url":
            item = {"type": "video_url", "video_url": {"url": block.url or ""}}
        elif block.type == "audio_url":
            item = {"type": "audio_url", "audio_url": {"url": block.url or ""}}
        else:
            raise ValueError(f"不支持的内容类型: {block.type}")

        if block.role:
            item["role"] = block.role

        return item

    async def _submit_task(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        payload: dict[str, Any],
    ) -> str:
        """提交视频生成任务, 返回 task_id."""
        url = f"{base_url.rstrip('/')}{_SUBMIT_PATH}"
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=30.0,
        )
        response.raise_for_status()
        body = response.json()
        task_id = body.get("id")
        if not task_id:
            raise RuntimeError(f"视频生成任务提交失败, 响应缺少 id: {body}")
        return task_id

    async def _poll_task(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        task_id: str,
        max_wait: float,
    ) -> dict[str, Any]:
        """轮询任务状态直到完成."""
        url = f"{base_url.rstrip('/')}{_POLL_PATH.format(task_id=task_id)}"
        elapsed = 0.0

        while elapsed < max_wait:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30.0,
            )
            response.raise_for_status()
            body = response.json()

            status = body.get("status", "")
            if status == "succeeded":
                return body
            if status in {"failed", "expired"}:
                error_msg = body.get("error", {})
                raise RuntimeError(
                    f"视频生成任务{status}: task_id={task_id}, error={error_msg}"
                )

            await asyncio.sleep(_DEFAULT_POLL_INTERVAL)
            elapsed += _DEFAULT_POLL_INTERVAL

        raise TimeoutError(f"视频生成任务轮询超时({max_wait}s): task_id={task_id}")

    async def _download_video(
        self,
        client: httpx.AsyncClient,
        task_result: dict[str, Any],
        task_id: str,
    ) -> GeneratedVideo:
        """从任务结果中提取视频URL并下载."""
        output = task_result.get("content", {})
        video_url = None
        video_duration = None

        if isinstance(output, dict):
            video_url = output.get("video_url")
            video_duration = output.get("duration")

        if not video_url:
            data_list = task_result.get("data") or []
            for item in data_list:
                if item.get("type") == "video_url":
                    video_info = item.get("video_url", {})
                    video_url = video_info.get("url")
                    break

        if not video_url:
            raise RuntimeError(f"视频生成任务成功但未找到视频URL: task_id={task_id}")

        logger.info("Seedance 视频下载中: task_id=%s", task_id)
        response = await client.get(video_url, timeout=120.0)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "video/mp4")
        mime_type = content_type.split(";", 1)[0].strip() or "video/mp4"

        usage = task_result.get("usage")
        return GeneratedVideo(
            video_data=response.content,
            mime_type=mime_type,
            task_id=task_id,
            duration=video_duration,
            usage=usage if isinstance(usage, dict) else None,
        )

    @staticmethod
    def _record_video_usage(
        *,
        metadata: Any,
        request: VideoGenerationRequest,
        task_id: str | None,
        usage: dict[str, Any] | None,
        success: bool,
        duration_ms: int,
    ) -> None:
        output_tokens = None
        total_tokens = None
        accuracy = "unknown"
        if usage:
            completion_tokens = usage.get("completion_tokens")
            total = usage.get("total_tokens")
            if isinstance(completion_tokens, int):
                output_tokens = completion_tokens
            if isinstance(total, int):
                total_tokens = total
            if output_tokens is not None or total_tokens is not None:
                accuracy = "exact"

        record_usage_from_context(
            operation="video_generation",
            provider=metadata.provider,
            model_id=metadata.id,
            unit_type="token",
            request_count=1,
            input_tokens=0 if usage else None,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            accuracy=accuracy,
            success=success,
            duration_ms=duration_ms,
            raw_usage=usage,
            external_job_id=task_id,
            metadata={
                "ratio": request.ratio,
                "duration": request.duration,
                "resolution": request.resolution,
                "generate_audio": request.generate_audio,
                "content_types": [block.type for block in request.content_blocks],
            },
        )

    @staticmethod
    def _get_api_key(env_name: str | None) -> str:
        try:
            return require_api_key_env(env_name, purpose="视频生成模型")
        except RuntimeError as e:
            raise ValueError(str(e)) from e


__all__ = [
    "GeneratedVideo",
    "VideoContentBlock",
    "VideoGenerationRequest",
    "VideoGenerationService",
]
