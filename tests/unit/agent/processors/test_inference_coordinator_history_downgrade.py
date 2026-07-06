"""InferenceCoordinator 历史图片降级单元测试.

覆盖 _downgrade_history_images_for_text_model 及其辅助函数:
- 多模态模型原样返回 (image_url 块保留)
- 非多模态模型把 image_url 块降级为 [图片: brief] / [图片]
- 反查命中/未命中/无 service 的边界
- _decode_data_uri / _history_has_image_blocks 纯函数

Mock 策略: get_model / create_attachment_registry_service 用 patch,
FileDeduplicationService.compute_hash 走真实计算 (保证 hash 真实).
"""

from __future__ import annotations

import base64 as b64mod
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from src.agent.processors.inference_coordinator import (
    InferenceCoordinator,
    _decode_data_uri,
    _history_has_image_blocks,
)


@pytest.fixture
def coordinator() -> InferenceCoordinator:
    return InferenceCoordinator({"llm": {"model": "test"}})


def _make_data_uri(payload: bytes = b"fake-image") -> str:
    """构造 base64 data URI."""
    b64 = b64mod.b64encode(payload).decode()
    return f"data:image/png;base64,{b64}"


def _make_image_message(
    text: str = "看这张图",
    url: str | None = None,
) -> HumanMessage:
    """构造含 image_url 块的 HumanMessage."""
    return HumanMessage(
        content=[
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": url or _make_data_uri()}},
        ],
    )


def _mock_model_meta(multimodal: bool) -> MagicMock:
    """构造模型元数据 mock, 控制多模态能力."""
    meta = MagicMock()
    meta.supports_multimodal.return_value = multimodal
    return meta


# ========== _decode_data_uri ==========


class TestDecodeDataUri:
    def test_should_decode_valid_data_uri(self):
        uri = _make_data_uri(b"hello")
        assert _decode_data_uri(uri) == b"hello"

    def test_should_return_none_for_http_url(self):
        assert _decode_data_uri("https://example.com/img.png") is None

    def test_should_return_none_for_plain_text(self):
        assert _decode_data_uri("not a uri") is None

    def test_should_return_none_for_invalid_base64(self):
        assert _decode_data_uri("data:image/png;base64,@@invalid@@") is None


# ========== _history_has_image_blocks ==========


class TestHistoryHasImageBlocks:
    def test_should_detect_image_url_block(self):
        assert _history_has_image_blocks([_make_image_message()]) is True

    def test_should_return_false_for_text_only(self):
        assert _history_has_image_blocks([HumanMessage(content="纯文本")]) is False

    def test_should_return_false_for_empty(self):
        assert _history_has_image_blocks([]) is False


# ========== _downgrade_history_images_for_text_model ==========


class TestDowngradeHistoryImages:
    async def test_multimodal_model_returns_history_unchanged(self, coordinator):
        """多模态模型应原样返回, image_url 块保留."""
        msg = _make_image_message()
        with patch(
            "src.inference.llm.definitions.model_registry.get_model",
            return_value=_mock_model_meta(True),
        ):
            result = await coordinator._downgrade_history_images_for_text_model(
                [msg],
                "vision-model",
                "u1",
                "t1",
                "a1",
            )
        assert result is not None
        assert len(result) == 1
        types = [b["type"] for b in result[0].content]
        assert "image_url" in types

    async def test_text_model_downgrades_to_brief_when_hit(self, coordinator):
        """非多模态 + 反查命中, 降级为 [图片: brief]."""
        msg = _make_image_message()
        mock_service = AsyncMock()
        mock_service.find_by_content_hash.return_value = MagicMock(brief="一张收据")
        with (
            patch(
                "src.inference.llm.definitions.model_registry.get_model",
                return_value=_mock_model_meta(False),
            ),
            patch(
                "src.storage.service.file_registry_service.create_file_registry_service",
                return_value=mock_service,
            ),
        ):
            result = await coordinator._downgrade_history_images_for_text_model(
                [msg],
                "text-model",
                "u1",
                "t1",
                "a1",
            )
        assert result is not None
        content = result[0].content
        assert all(b["type"] == "text" for b in content)
        assert any("[图片: 一张收据]" in b["text"] for b in content)

    async def test_text_model_downgrades_to_placeholder_when_miss(self, coordinator):
        """非多模态 + 反查未命中, 降级为 [图片]."""
        msg = _make_image_message()
        mock_service = AsyncMock()
        mock_service.find_by_content_hash.return_value = None
        with (
            patch(
                "src.inference.llm.definitions.model_registry.get_model",
                return_value=_mock_model_meta(False),
            ),
            patch(
                "src.storage.service.file_registry_service.create_file_registry_service",
                return_value=mock_service,
            ),
        ):
            result = await coordinator._downgrade_history_images_for_text_model(
                [msg],
                "text-model",
                "u1",
                "t1",
                "a1",
            )
        assert result is not None
        content = result[0].content
        assert all(b["type"] == "text" for b in content)
        assert any("[图片]" in b["text"] for b in content)

    async def test_text_model_placeholder_when_no_agent_id(self, coordinator):
        """agent_id 为空时 service 仍创建 (用户级), 但反查无果降级为 [图片]."""
        msg = _make_image_message()
        mock_service = AsyncMock()
        mock_service.find_by_content_hash = AsyncMock(return_value=None)
        with (
            patch(
                "src.inference.llm.definitions.model_registry.get_model",
                return_value=_mock_model_meta(False),
            ),
            patch(
                "src.storage.service.file_registry_service.create_file_registry_service",
                return_value=mock_service,
            ),
        ):
            result = await coordinator._downgrade_history_images_for_text_model(
                [msg],
                "text-model",
                "u1",
                "t1",
                None,
            )
        assert result is not None
        content = result[0].content
        assert any("[图片]" in b["text"] for b in content)

    async def test_text_model_placeholder_for_http_url(self, coordinator):
        """image_url 为 http URL (非 data URI) 时无法算 hash, 降级为 [图片]."""
        msg = _make_image_message(url="https://example.com/img.png")
        mock_service = AsyncMock()
        with (
            patch(
                "src.inference.llm.definitions.model_registry.get_model",
                return_value=_mock_model_meta(False),
            ),
            patch(
                "src.storage.service.file_registry_service.create_file_registry_service",
                return_value=mock_service,
            ),
        ):
            result = await coordinator._downgrade_history_images_for_text_model(
                [msg],
                "text-model",
                "u1",
                "t1",
                "a1",
            )
        assert result is not None
        content = result[0].content
        assert any("[图片]" in b["text"] for b in content)
        mock_service.find_by_content_hash.assert_not_called()

    async def test_text_model_no_images_returns_unchanged(self, coordinator):
        """非多模态但历史无图片, 原样返回."""
        msg = HumanMessage(content="纯文本历史")
        with patch(
            "src.inference.llm.definitions.model_registry.get_model",
            return_value=_mock_model_meta(False),
        ):
            result = await coordinator._downgrade_history_images_for_text_model(
                [msg],
                "text-model",
                "u1",
                "t1",
                "a1",
            )
        assert result is not None
        assert result[0].content == "纯文本历史"

    async def test_none_history_returns_none(self, coordinator):
        with patch(
            "src.inference.llm.definitions.model_registry.get_model",
            return_value=_mock_model_meta(False),
        ):
            result = await coordinator._downgrade_history_images_for_text_model(
                None,
                "text-model",
                "u1",
                "t1",
                "a1",
            )
        assert result is None

    async def test_text_model_preserves_text_blocks(self, coordinator):
        """降级后原有的 text 块保留不变."""
        msg = _make_image_message(text="原始文字")
        mock_service = AsyncMock()
        mock_service.find_by_content_hash.return_value = MagicMock(brief="描述")
        with (
            patch(
                "src.inference.llm.definitions.model_registry.get_model",
                return_value=_mock_model_meta(False),
            ),
            patch(
                "src.storage.service.file_registry_service.create_file_registry_service",
                return_value=mock_service,
            ),
        ):
            result = await coordinator._downgrade_history_images_for_text_model(
                [msg],
                "text-model",
                "u1",
                "t1",
                "a1",
            )
        assert result is not None
        content = result[0].content
        assert any(b["text"] == "原始文字" for b in content)

    async def test_unknown_model_treated_as_text(self, coordinator):
        """get_model 返回 None (未知模型) 时按非多模态处理, 安全降级."""
        msg = _make_image_message()
        with (
            patch(
                "src.inference.llm.definitions.model_registry.get_model",
                return_value=None,
            ),
            patch(
                "src.storage.service.file_registry_service.create_file_registry_service",
                return_value=AsyncMock(
                    find_by_content_hash=AsyncMock(return_value=None)
                ),
            ),
        ):
            result = await coordinator._downgrade_history_images_for_text_model(
                [msg],
                "unknown-model",
                "u1",
                "t1",
                "a1",
            )
        assert result is not None
        content = result[0].content
        assert all(b["type"] == "text" for b in content)
