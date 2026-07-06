"""视频生成服务单元测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.inference.llm.definitions import ModelMetadata, ModelType
from src.inference.llm.definitions.model_types import ModelCapability
from src.inference.video_generation import (
    VideoContentBlock,
    VideoGenerationRequest,
    VideoGenerationService,
)


def _make_video_model() -> ModelMetadata:
    return ModelMetadata(
        id="doubao:doubao-seedance-2-0-260128",
        name="Seedance 2.0",
        provider="doubao",
        model_type=ModelType.VIDEO_GENERATION,
        description="视频生成模型",
        model_params={
            "ratio": {"default": "adaptive"},
            "duration": {"default": 5},
            "generate_audio": {"default": True},
        },
        capabilities=[ModelCapability.TEXT_INPUT, ModelCapability.VIDEO_GENERATION],
    )


def _make_submit_response(task_id: str = "task-123") -> httpx.Response:
    return httpx.Response(
        200,
        json={"id": task_id},
        request=httpx.Request("POST", "https://example.test/contents/generations/tasks"),
    )


def _make_poll_response(
    status: str,
    video_url: str | None = None,
    duration: int | None = None,
    usage: dict | None = None,
) -> httpx.Response:
    content: dict = {}
    if video_url:
        content["video_url"] = video_url
    if duration is not None:
        content["duration"] = duration
    body: dict = {"id": "task-123", "status": status, "content": content}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(
        200,
        json=body,
        request=httpx.Request("GET", "https://example.test/contents/generations/tasks/task-123"),
    )


def _make_video_download_response(video_bytes: bytes = b"fake-video") -> httpx.Response:
    return httpx.Response(
        200,
        content=video_bytes,
        headers={"content-type": "video/mp4"},
        request=httpx.Request("GET", "https://cdn.test/video.mp4"),
    )


def _setup_mocks(
    video_bytes: bytes = b"fake-video",
    task_id: str = "task-123",
    poll_statuses: list[str] | None = None,
    usage: dict | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """创建统一的 mock 环境, 返回 (client, http_manager, provider)."""
    if poll_statuses is None:
        poll_statuses = ["succeeded"]

    client = MagicMock()
    client.post = AsyncMock(return_value=_make_submit_response(task_id))

    poll_responses = [
        _make_poll_response(
            s,
            video_url="https://cdn.test/video.mp4" if s == "succeeded" else None,
            duration=5 if s == "succeeded" else None,
            usage=usage if s == "succeeded" else None,
        )
        for s in poll_statuses
    ]
    download_response = _make_video_download_response(video_bytes)
    client.get = AsyncMock(return_value=None)
    client.get.side_effect = [*poll_responses, download_response]

    http_manager = MagicMock()
    http_manager.get.return_value = client

    provider = MagicMock()
    provider.api_key_env = "ARK_API_KEY"
    provider.get_effective_base_url.return_value = "https://ark.test/api/v3"

    return client, http_manager, provider


@pytest.mark.asyncio
async def test_text_to_video_full_flow() -> None:
    """文生视频: 应完成 提交->轮询->下载 全流程."""
    service = VideoGenerationService()
    client, http_manager, provider = _setup_mocks(
        poll_statuses=["queued", "running", "succeeded"],
        usage={"completion_tokens": 123, "total_tokens": 456},
    )

    request = VideoGenerationRequest(
        content_blocks=[VideoContentBlock(type="text", text="一只猫在窗边打哈欠")],
        duration=5,
    )

    with (
        patch("src.inference.video_generation.get_model", return_value=_make_video_model()),
        patch("src.inference.video_generation.get_provider_config", return_value=provider),
        patch("src.inference.video_generation.get_http_pool", return_value=http_manager),
        patch.dict("os.environ", {"ARK_API_KEY": "test-key"}),
        patch("src.inference.video_generation._DEFAULT_POLL_INTERVAL", 0.01),
        patch("src.inference.video_generation.record_usage_from_context") as record_usage,
    ):
        result = await service.generate_video(
            model_id="doubao:doubao-seedance-2-0-260128",
            request=request,
        )

    assert result.video_data == b"fake-video"
    assert result.mime_type == "video/mp4"
    assert result.task_id == "task-123"
    assert result.usage == {"completion_tokens": 123, "total_tokens": 456}
    assert client.post.await_count == 1
    record_usage.assert_called_once()
    assert record_usage.call_args.kwargs["operation"] == "video_generation"
    assert record_usage.call_args.kwargs["output_tokens"] == 123
    assert record_usage.call_args.kwargs["total_tokens"] == 456
    assert record_usage.call_args.kwargs["input_tokens"] == 0
    assert record_usage.call_args.kwargs["accuracy"] == "exact"


@pytest.mark.asyncio
async def test_image_to_video_with_first_frame() -> None:
    """图生视频-首帧: 应将图片内容块序列化到请求体."""
    service = VideoGenerationService()
    client, http_manager, provider = _setup_mocks()

    request = VideoGenerationRequest(
        content_blocks=[
            VideoContentBlock(type="text", text="猫在打哈欠"),
            VideoContentBlock(
                type="image_url",
                url="data:image/png;base64,iVBOR...",
                role="first_frame",
            ),
        ],
        duration=5,
    )

    with (
        patch("src.inference.video_generation.get_model", return_value=_make_video_model()),
        patch("src.inference.video_generation.get_provider_config", return_value=provider),
        patch("src.inference.video_generation.get_http_pool", return_value=http_manager),
        patch.dict("os.environ", {"ARK_API_KEY": "test-key"}),
        patch("src.inference.video_generation._DEFAULT_POLL_INTERVAL", 0.01),
        patch("src.inference.video_generation.record_usage_from_context"),
    ):
        result = await service.generate_video(
            model_id="doubao:doubao-seedance-2-0-260128",
            request=request,
        )

    assert result.video_data == b"fake-video"

    call_args = client.post.call_args
    payload = call_args.kwargs["json"]
    assert len(payload["content"]) == 2
    assert payload["content"][0]["type"] == "text"
    assert payload["content"][1]["type"] == "image_url"
    assert payload["content"][1]["role"] == "first_frame"


@pytest.mark.asyncio
async def test_multimodal_reference_with_video_and_audio() -> None:
    """多模态参考: 应正确序列化图片+视频+音频内容块."""
    service = VideoGenerationService()
    client, http_manager, provider = _setup_mocks()

    request = VideoGenerationRequest(
        content_blocks=[
            VideoContentBlock(type="text", text="参考素材生成视频"),
            VideoContentBlock(
                type="image_url",
                url="https://example.com/ref.jpg",
                role="reference_image",
            ),
            VideoContentBlock(
                type="video_url",
                url="https://example.com/ref.mp4",
                role="reference_video",
            ),
            VideoContentBlock(
                type="audio_url",
                url="https://example.com/voice.wav",
                role="reference_audio",
            ),
        ],
        duration=10,
    )

    with (
        patch("src.inference.video_generation.get_model", return_value=_make_video_model()),
        patch("src.inference.video_generation.get_provider_config", return_value=provider),
        patch("src.inference.video_generation.get_http_pool", return_value=http_manager),
        patch.dict("os.environ", {"ARK_API_KEY": "test-key"}),
        patch("src.inference.video_generation._DEFAULT_POLL_INTERVAL", 0.01),
        patch("src.inference.video_generation.record_usage_from_context"),
    ):
        result = await service.generate_video(
            model_id="doubao:doubao-seedance-2-0-260128",
            request=request,
        )

    assert result.video_data == b"fake-video"

    call_args = client.post.call_args
    payload = call_args.kwargs["json"]
    assert len(payload["content"]) == 4
    assert payload["content"][2]["type"] == "video_url"
    assert payload["content"][2]["role"] == "reference_video"
    assert payload["content"][3]["type"] == "audio_url"
    assert payload["content"][3]["role"] == "reference_audio"


@pytest.mark.asyncio
async def test_rejects_non_video_model() -> None:
    """非视频生成模型应被拒绝."""
    service = VideoGenerationService()
    model = ModelMetadata(
        id="doubao:chat",
        name="Chat",
        provider="doubao",
        model_type=ModelType.CHAT,
        description="对话模型",
        model_params={},
        capabilities=[ModelCapability.TEXT_INPUT],
    )

    request = VideoGenerationRequest(
        content_blocks=[VideoContentBlock(type="text", text="测试")],
    )

    with (
        patch("src.inference.video_generation.get_model", return_value=model),
        pytest.raises(ValueError, match="不支持视频生成"),
    ):
        await service.generate_video(
            model_id="doubao:chat",
            request=request,
        )


@pytest.mark.asyncio
async def test_rejects_empty_content() -> None:
    """空内容块应抛出异常."""
    service = VideoGenerationService()

    request = VideoGenerationRequest(content_blocks=[])

    with (
        patch("src.inference.video_generation.get_model", return_value=_make_video_model()),
        pytest.raises(ValueError, match="缺少内容"),
    ):
        await service.generate_video(
            model_id="doubao:doubao-seedance-2-0-260128",
            request=request,
        )


@pytest.mark.asyncio
async def test_handles_task_failure() -> None:
    """任务失败时应抛出异常."""
    service = VideoGenerationService()
    client, http_manager, provider = _setup_mocks(poll_statuses=["failed"])

    request = VideoGenerationRequest(
        content_blocks=[VideoContentBlock(type="text", text="测试")],
    )

    with (
        patch("src.inference.video_generation.get_model", return_value=_make_video_model()),
        patch("src.inference.video_generation.get_provider_config", return_value=provider),
        patch("src.inference.video_generation.get_http_pool", return_value=http_manager),
        patch.dict("os.environ", {"ARK_API_KEY": "test-key"}),
        patch("src.inference.video_generation._DEFAULT_POLL_INTERVAL", 0.01),
        patch("src.inference.video_generation.record_usage_from_context") as record_usage,
        pytest.raises(RuntimeError, match="任务failed"),
    ):
        await service.generate_video(
            model_id="doubao:doubao-seedance-2-0-260128",
            request=request,
        )
    record_usage.assert_called_once()
    assert record_usage.call_args.kwargs["operation"] == "video_generation"
    assert record_usage.call_args.kwargs["success"] is False
    assert record_usage.call_args.kwargs["accuracy"] == "unknown"


@pytest.mark.asyncio
async def test_handles_task_expired() -> None:
    """任务超时(expired)时应抛出异常."""
    service = VideoGenerationService()
    client, http_manager, provider = _setup_mocks(poll_statuses=["expired"])

    request = VideoGenerationRequest(
        content_blocks=[VideoContentBlock(type="text", text="测试")],
    )

    with (
        patch("src.inference.video_generation.get_model", return_value=_make_video_model()),
        patch("src.inference.video_generation.get_provider_config", return_value=provider),
        patch("src.inference.video_generation.get_http_pool", return_value=http_manager),
        patch.dict("os.environ", {"ARK_API_KEY": "test-key"}),
        patch("src.inference.video_generation._DEFAULT_POLL_INTERVAL", 0.01),
        patch("src.inference.video_generation.record_usage_from_context"),
        pytest.raises(RuntimeError, match="任务expired"),
    ):
        await service.generate_video(
            model_id="doubao:doubao-seedance-2-0-260128",
            request=request,
        )


def test_serialize_content_block_text() -> None:
    """文本内容块序列化."""
    service = VideoGenerationService()
    block = VideoContentBlock(type="text", text="测试")
    result = service._serialize_content_block(block)
    assert result == {"type": "text", "text": "测试"}


def test_serialize_content_block_image_with_role() -> None:
    """图片内容块应包含role."""
    service = VideoGenerationService()
    block = VideoContentBlock(
        type="image_url",
        url="https://example.com/img.jpg",
        role="first_frame",
    )
    result = service._serialize_content_block(block)
    assert result == {
        "type": "image_url",
        "image_url": {"url": "https://example.com/img.jpg"},
        "role": "first_frame",
    }


def test_serialize_content_block_video_without_role() -> None:
    """视频内容块无role时不应包含role字段."""
    service = VideoGenerationService()
    block = VideoContentBlock(type="video_url", url="https://example.com/v.mp4")
    result = service._serialize_content_block(block)
    assert "role" not in result
    assert result["type"] == "video_url"


def test_serialize_content_block_unknown_type() -> None:
    """未知类型应抛出异常."""
    service = VideoGenerationService()
    block = VideoContentBlock(type="unknown", url="test")
    with pytest.raises(ValueError, match="不支持的内容类型"):
        service._serialize_content_block(block)
