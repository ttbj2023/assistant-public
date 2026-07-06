"""统一健康数据提取器单元测试.

测试 src/inference/health_data_extraction/unified_extractor.py 的功能:
- _parse_response: JSON解析逻辑
- ExtractionResult: 结果数据类
- is_available: model_id检查
- _load_prompt_template: 模板加载(需要mock文件系统)

Mock边界:
- Mock配置系统获取model_id
- Mock create_llm (LLM调用)
- 保留真实解析和验证逻辑
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.inference.health_data_extraction.unified_extractor import (
    _VALID_DATA_TYPES,
    ExtractionResult,
    UnifiedHealthExtractor,
)


class TestExtractionResult:
    def test_should_store_data_type_and_data(self):
        """测试应存储数据类型和数据."""
        data = {"food_name": "米饭", "calories": 200}
        result = ExtractionResult(data_type="meal_record", data=data)

        assert result.data_type == "meal_record"
        assert result.data == data

    def test_repr_should_show_type(self):
        """测试repr应显示类型."""
        result = ExtractionResult(data_type="weight_record", data={})
        assert "weight_record" in repr(result)


class TestValidDataTypes:
    def test_should_contain_all_expected_types(self):
        """测试应包含所有预期数据类型."""
        expected = {
            "meal_record",
            "food_product",
            "shopping_list",
            "weight_record",
            "workout_record",
            "medical_report",
        }
        assert expected == _VALID_DATA_TYPES


class TestUnifiedHealthExtractorParseResponse:
    @pytest.fixture
    def extractor(self):
        """创建Mock环境下的提取器实例."""
        with patch(
            "src.inference.health_data_extraction.unified_extractor._load_prompt_template",
            return_value="test template {user_message} {current_date}",
        ):
            return UnifiedHealthExtractor()

    def test_should_parse_valid_extractions(self, extractor):
        """测试应解析有效提取结果."""
        raw = {
            "extractions": [
                {"data_type": "weight_record", "data": {"weight_kg": 70.5}},
                {"data_type": "meal_record", "data": {"items": []}},
            ]
        }

        results = extractor._parse_response(raw)

        assert len(results) == 2
        assert results[0].data_type == "weight_record"
        assert results[0].data["weight_kg"] == 70.5
        assert results[1].data_type == "meal_record"

    def test_should_skip_invalid_data_type(self, extractor):
        """测试应跳过无效数据类型."""
        raw = {
            "extractions": [
                {"data_type": "invalid_type", "data": {"key": "value"}},
                {"data_type": "weight_record", "data": {"weight_kg": 70}},
            ]
        }

        results = extractor._parse_response(raw)

        assert len(results) == 1
        assert results[0].data_type == "weight_record"

    def test_should_skip_none_data(self, extractor):
        """测试应跳过data为None的条目."""
        raw = {
            "extractions": [
                {"data_type": "weight_record", "data": None},
            ]
        }

        results = extractor._parse_response(raw)

        assert len(results) == 0

    def test_should_skip_non_dict_items(self, extractor):
        """测试应跳过非字典条目."""
        raw = {
            "extractions": [
                "not a dict",
                42,
                {"data_type": "meal_record", "data": {"items": []}},
            ]
        }

        results = extractor._parse_response(raw)

        assert len(results) == 1

    def test_should_handle_empty_extractions(self, extractor):
        """测试应处理空extractions."""
        raw = {"extractions": []}

        results = extractor._parse_response(raw)

        assert len(results) == 0

    def test_should_handle_non_list_extractions(self, extractor):
        """测试应处理非数组extractions."""
        raw = {"extractions": "not a list"}

        results = extractor._parse_response(raw)

        assert len(results) == 0

    def test_should_handle_missing_extractions_key(self, extractor):
        """测试应处理缺失extractions键."""
        raw = {"other_key": "value"}

        results = extractor._parse_response(raw)

        assert len(results) == 0


class TestUnifiedHealthExtractorAvailability:
    def test_should_be_available_with_model_id(self):
        """测试有model_id时应可用."""
        with patch(
            "src.inference.health_data_extraction.unified_extractor._get_model_config",
            return_value={
                "model": "deepseek:deepseek-v4-flash",
                "timeout": 60.0,
            },
        ):
            with patch(
                "src.inference.health_data_extraction.unified_extractor._load_prompt_template",
                return_value="template",
            ):
                extractor = UnifiedHealthExtractor()
                assert extractor.is_available() is True

    def test_should_not_be_available_without_model_id(self):
        """测试无model_id时应不可用."""
        with patch(
            "src.inference.health_data_extraction.unified_extractor._get_model_config",
            return_value={"model": "", "timeout": 60.0},
        ):
            with patch(
                "src.inference.health_data_extraction.unified_extractor._load_prompt_template",
                return_value="template",
            ):
                extractor = UnifiedHealthExtractor()
                assert extractor.is_available() is False


class TestUnifiedHealthExtractorExtract:
    @pytest.mark.asyncio
    async def test_should_return_empty_when_not_available(self):
        """测试不可用时应返回空列表."""
        with patch(
            "src.inference.health_data_extraction.unified_extractor._get_model_config",
            return_value={"model": "", "timeout": 60.0},
        ):
            with patch(
                "src.inference.health_data_extraction.unified_extractor._load_prompt_template",
                return_value="template",
            ):
                extractor = UnifiedHealthExtractor()
                results = await extractor.extract("我今天吃了两碗米饭")
                assert results == []

    @pytest.mark.asyncio
    async def test_should_call_llm_and_parse(self):
        """测试应调用LLM并解析结果."""
        import json

        llm_raw = {
            "extractions": [
                {"data_type": "weight_record", "data": {"weight_kg": 72}},
            ]
        }
        mock_response = AsyncMock(content=json.dumps(llm_raw))
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm.bind = MagicMock(return_value=mock_llm)

        with patch(
            "src.inference.health_data_extraction.unified_extractor._get_model_config",
            return_value={
                "model": "deepseek:deepseek-v4-flash",
                "timeout": 60.0,
            },
        ):
            with patch(
                "src.inference.health_data_extraction.unified_extractor._load_prompt_template",
                return_value="template {user_message} {current_date}",
            ):
                with patch(
                    "src.inference.llm.model_loader.create_llm",
                    return_value=mock_llm,
                ):
                    extractor = UnifiedHealthExtractor()
                    results = await extractor.extract("今天体重72公斤")
                    assert len(results) == 1
                    assert results[0].data_type == "weight_record"
                    assert results[0].data["weight_kg"] == 72

    @pytest.mark.asyncio
    async def test_should_return_empty_on_failure(self):
        """测试主模型失败时应返回空列表."""

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("主模型失败"))
        mock_llm.bind = MagicMock(return_value=mock_llm)

        with patch(
            "src.inference.health_data_extraction.unified_extractor._get_model_config",
            return_value={
                "model": "deepseek:deepseek-v4-flash",
                "timeout": 60.0,
            },
        ):
            with patch(
                "src.inference.health_data_extraction.unified_extractor._load_prompt_template",
                return_value="template {user_message} {current_date}",
            ):
                with patch(
                    "src.inference.llm.model_loader.create_llm",
                    return_value=mock_llm,
                ):
                    extractor = UnifiedHealthExtractor()
                    results = await extractor.extract("测试")
                    assert results == []
