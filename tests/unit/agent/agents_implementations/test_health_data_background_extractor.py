"""后台健康数据自动提取器单元测试.

覆盖 HealthDataBackgroundExtractor 的核心逻辑:
- extract_from_conversation (Mock UnifiedHealthExtractor + service)
- _store_results (Mock service)
- 提取器不可用时跳过
- 空结果时跳过
- 图片描述拼接
- Fire-and-forget 异常处理
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.agents_implementations.health_assistant.health_data_background_extractor import (
    HealthDataBackgroundExtractor,
)


@pytest.fixture
def extractor() -> HealthDataBackgroundExtractor:
    return HealthDataBackgroundExtractor("user1", "thread1", agent_id="health")


class TestExtractFromConversation:
    """主入口测试."""

    @pytest.mark.asyncio
    async def test_extractor_not_available(
        self, extractor: HealthDataBackgroundExtractor
    ) -> None:
        mock_extractor = MagicMock()
        mock_extractor.is_available.return_value = False

        with patch(
            "src.inference.health_data_extraction.unified_extractor.UnifiedHealthExtractor",
            return_value=mock_extractor,
        ):
            await extractor.extract_from_conversation("我吃了米饭")

    @pytest.mark.asyncio
    async def test_no_health_data_found(
        self, extractor: HealthDataBackgroundExtractor
    ) -> None:
        mock_extractor = MagicMock()
        mock_extractor.is_available.return_value = True
        mock_extractor.extract = AsyncMock(return_value=[])

        with patch(
            "src.inference.health_data_extraction.unified_extractor.UnifiedHealthExtractor",
            return_value=mock_extractor,
        ):
            await extractor.extract_from_conversation("今天天气不错")

    @pytest.mark.asyncio
    async def test_successful_extraction(
        self, extractor: HealthDataBackgroundExtractor
    ) -> None:
        mock_result = SimpleNamespace(
            data_type="weight_record", data={"weight_kg": 70.5}
        )
        mock_extractor = MagicMock()
        mock_extractor.is_available.return_value = True
        mock_extractor.extract = AsyncMock(return_value=[mock_result])

        mock_service = MagicMock()
        mock_service.store_extraction = AsyncMock(
            return_value={"success": True, "data_type": "weight_record"}
        )

        with (
            patch(
                "src.inference.health_data_extraction.unified_extractor.UnifiedHealthExtractor",
                return_value=mock_extractor,
            ),
            patch(
                "src.agent.agents_implementations.health_assistant.health_data_background_extractor"
                ".get_health_data_extraction_service",
                return_value=mock_service,
            ),
        ):
            await extractor.extract_from_conversation(
                "我今天体重70.5公斤", round_number=5
            )
            mock_service.store_extraction.assert_called_once_with(
                data_type="weight_record",
                data={"weight_kg": 70.5},
                round_number=5,
            )

    @pytest.mark.asyncio
    async def test_with_attachment_infos(
        self, extractor: HealthDataBackgroundExtractor
    ) -> None:
        mock_extractor = MagicMock()
        mock_extractor.is_available.return_value = True
        mock_extractor.extract = AsyncMock(return_value=[])

        infos = [
            SimpleNamespace(detail="食物照片, 有米饭和蔬菜"),
            SimpleNamespace(detail="图片"),
        ]

        with patch(
            "src.inference.health_data_extraction.unified_extractor.UnifiedHealthExtractor",
            return_value=mock_extractor,
        ):
            await extractor.extract_from_conversation(
                "我吃了这个", attachment_infos=infos
            )

            call_args = mock_extractor.extract.call_args[0][0]
            assert "[图片1描述]:" in call_args
            assert "食物照片" in call_args

    @pytest.mark.asyncio
    async def test_exception_fire_and_forget(
        self, extractor: HealthDataBackgroundExtractor
    ) -> None:
        with patch(
            "src.inference.health_data_extraction.unified_extractor.UnifiedHealthExtractor",
            side_effect=Exception("Init failed"),
        ):
            await extractor.extract_from_conversation("test")


class TestStoreResults:
    """存储结果测试."""

    @pytest.mark.asyncio
    async def test_store_success(
        self, extractor: HealthDataBackgroundExtractor
    ) -> None:
        results = [
            SimpleNamespace(data_type="weight_record", data={"weight_kg": 65}),
        ]
        mock_service = MagicMock()
        mock_service.store_extraction = AsyncMock(
            return_value={"success": True, "data_type": "weight_record"}
        )

        with patch(
            "src.agent.agents_implementations.health_assistant.health_data_background_extractor"
            ".get_health_data_extraction_service",
            return_value=mock_service,
        ):
            await extractor._store_results(results, round_number=3)
            mock_service.store_extraction.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_failure_continues(
        self, extractor: HealthDataBackgroundExtractor
    ) -> None:
        results = [
            SimpleNamespace(data_type="weight_record", data={"weight_kg": 65}),
            SimpleNamespace(data_type="meal_record", data={"meal_type": "lunch"}),
        ]
        mock_service = MagicMock()
        mock_service.store_extraction = AsyncMock(
            side_effect=[
                Exception("DB error"),
                {"success": True},
            ]
        )

        with patch(
            "src.agent.agents_implementations.health_assistant.health_data_background_extractor"
            ".get_health_data_extraction_service",
            return_value=mock_service,
        ):
            await extractor._store_results(results)
            assert mock_service.store_extraction.call_count == 2

    @pytest.mark.asyncio
    async def test_store_returns_failure(
        self, extractor: HealthDataBackgroundExtractor
    ) -> None:
        results = [
            SimpleNamespace(data_type="weight_record", data={"weight_kg": 65}),
        ]
        mock_service = MagicMock()
        mock_service.store_extraction = AsyncMock(
            return_value={"success": False, "error": "验证失败"}
        )

        with patch(
            "src.agent.agents_implementations.health_assistant.health_data_background_extractor"
            ".get_health_data_extraction_service",
            return_value=mock_service,
        ):
            await extractor._store_results(results)
            mock_service.store_extraction.assert_called_once()
