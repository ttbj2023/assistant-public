"""图片生成服务单元测试."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.inference.image_generation import ImageGenerationService
from src.inference.llm.definitions import ModelMetadata, ModelType
from src.inference.llm.definitions.model_types import ModelCapability


def _make_image_model() -> ModelMetadata:
    return ModelMetadata(
        id="doubao:doubao-seedream-5-0-260128",
        name="Seedream",
        provider="doubao",
        model_type=ModelType.IMAGE_GENERATION,
        description="图片生成模型",
        model_params={"size": {"default": "2048x2048"}},
        capabilities=[ModelCapability.TEXT_INPUT, ModelCapability.IMAGE_GENERATION],
    )


@pytest.mark.asyncio
async def test_generate_image_decodes_b64_json() -> None:
    """应解析b64_json图片结果."""
    service = ImageGenerationService()
    image_bytes = b"fake-image"
    response = httpx.Response(
        200,
        json={"data": [{"b64_json": base64.b64encode(image_bytes).decode()}]},
        request=httpx.Request("POST", "https://example.test/images/generations"),
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    http_manager = MagicMock()
    http_manager.get.return_value = client
    provider = MagicMock()
    provider.api_key_env = "ARK_API_KEY"
    provider.get_effective_base_url.return_value = "https://ark.test/api/v3"

    with (
        patch(
            "src.inference.image_generation.get_model", return_value=_make_image_model()
        ),
        patch(
            "src.inference.image_generation.get_provider_config", return_value=provider
        ),
        patch(
            "src.inference.image_generation.get_http_pool",
            return_value=http_manager,
        ),
        patch("src.inference.image_generation.record_usage_from_context") as record_usage,
        patch.dict("os.environ", {"ARK_API_KEY": "test-key"}),
    ):
        result = await service.generate_image(
            model_id="doubao:doubao-seedream-5-0-260128",
            prompt="画一只猫",
            size="2048x2048",
        )

    assert result.image_data == image_bytes
    assert result.mime_type == "image/png"
    client.post.assert_awaited_once()
    record_usage.assert_called_once()
    assert record_usage.call_args.kwargs["operation"] == "image_generation"
    assert record_usage.call_args.kwargs["unit_type"] == "count"
    assert record_usage.call_args.kwargs["request_count"] == 1
    assert record_usage.call_args.kwargs["success"] is True


@pytest.mark.asyncio
async def test_generate_image_requires_image_generation_capability() -> None:
    """非图片生成模型应被拒绝."""
    service = ImageGenerationService()
    model = ModelMetadata(
        id="doubao:chat",
        name="Chat",
        provider="doubao",
        model_type=ModelType.CHAT,
        description="对话模型",
        model_params={},
        capabilities=[ModelCapability.TEXT_INPUT],
    )

    with (
        patch("src.inference.image_generation.get_model", return_value=model),
        pytest.raises(ValueError, match="不支持图片生成"),
    ):
        await service.generate_image(
            model_id="doubao:chat",
            prompt="画一只猫",
            size="2048x2048",
        )


@pytest.mark.asyncio
async def test_generate_image_records_failed_request() -> None:
    """图片接口失败时仍应记录一次请求."""
    service = ImageGenerationService()
    response = httpx.Response(
        500,
        json={"error": "boom"},
        request=httpx.Request("POST", "https://example.test/images/generations"),
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    http_manager = MagicMock()
    http_manager.get.return_value = client
    provider = MagicMock()
    provider.api_key_env = "ARK_API_KEY"
    provider.get_effective_base_url.return_value = "https://ark.test/api/v3"

    with (
        patch(
            "src.inference.image_generation.get_model", return_value=_make_image_model()
        ),
        patch(
            "src.inference.image_generation.get_provider_config", return_value=provider
        ),
        patch("src.inference.image_generation.get_http_pool", return_value=http_manager),
        patch("src.inference.image_generation.record_usage_from_context") as record_usage,
        patch.dict("os.environ", {"ARK_API_KEY": "test-key"}),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await service.generate_image(
            model_id="doubao:doubao-seedream-5-0-260128",
            prompt="画一只猫",
            size="2048x2048",
        )

    record_usage.assert_called_once()
    assert record_usage.call_args.kwargs["operation"] == "image_generation"
    assert record_usage.call_args.kwargs["success"] is False
    assert record_usage.call_args.kwargs["accuracy"] == "unknown"
