"""SimpleMemoryAnalyzer 单元测试.

覆盖范围:
- _validate_result: 合法/非法 operations 解析, 字段白名单(preferences/insights)
- _extract_json_from_response: 纯 JSON / 含包裹文本的 JSON
- analyze_memory_update: LLM mock 全流程 + 失败降级
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.inference.content_analyzer.simple_memory_analyzer import SimpleMemoryAnalyzer


class TestValidateResult:
    def test_valid_add_operations(self) -> None:
        data = {
            "has_operations": True,
            "operations": [
                {"action": "add", "field": "preferences", "content": "回复简洁"},
                {"action": "add", "field": "insights", "content": "认可三段式"},
            ],
        }
        result = SimpleMemoryAnalyzer._validate_result(data)
        assert result.has_operations is True
        assert len(result.operations) == 2

    def test_invalid_field_rejected(self) -> None:
        """basic_info 不在 simple 白名单, 应被拒绝."""
        data = {
            "has_operations": True,
            "operations": [
                {"action": "add", "field": "basic_info", "content": "x"},
                {"action": "add", "field": "preferences", "content": "ok"},
            ],
        }
        result = SimpleMemoryAnalyzer._validate_result(data)
        assert len(result.operations) == 1
        assert result.operations[0].field == "preferences"

    def test_invalid_action_rejected(self) -> None:
        data = {
            "operations": [
                {"action": "create", "field": "insights", "content": "x"},
            ],
        }
        result = SimpleMemoryAnalyzer._validate_result(data)
        assert result.has_operations is False

    def test_delete_requires_content(self) -> None:
        data = {
            "operations": [{"action": "delete", "field": "insights", "content": ""}]
        }
        result = SimpleMemoryAnalyzer._validate_result(data)
        assert len(result.operations) == 0

    def test_change_requires_old_and_new(self) -> None:
        data = {
            "operations": [
                {"action": "change", "field": "preferences", "old_content": "old"},
            ]
        }
        result = SimpleMemoryAnalyzer._validate_result(data)
        assert len(result.operations) == 0

    def test_empty_operations(self) -> None:
        result = SimpleMemoryAnalyzer._validate_result({"operations": []})
        assert result.has_operations is False

    def test_malformed_operations_list(self) -> None:
        result = SimpleMemoryAnalyzer._validate_result({"operations": "not a list"})
        assert result.has_operations is False


class TestExtractJson:
    def test_pure_json(self) -> None:
        data = SimpleMemoryAnalyzer._extract_json_from_response(
            json.dumps({"has_operations": False, "operations": []})
        )
        assert data["has_operations"] is False

    def test_json_in_text(self) -> None:
        content = '一些前置文本\n{"has_operations": true, "operations": []}\n后置'
        data = SimpleMemoryAnalyzer._extract_json_from_response(content)
        assert data["has_operations"] is True

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="JSON"):
            SimpleMemoryAnalyzer._extract_json_from_response("no json here")


class TestAnalyzeMemoryUpdate:
    @pytest.fixture
    def analyzer(self) -> SimpleMemoryAnalyzer:
        return SimpleMemoryAnalyzer(config_override={"model_id": "test-model"})

    @pytest.mark.asyncio
    async def test_success_returns_operations(
        self, analyzer: SimpleMemoryAnalyzer
    ) -> None:
        mock_response = type(
            "R",
            (),
            {
                "content": json.dumps({
                    "has_operations": True,
                    "operations": [
                        {"action": "add", "field": "insights", "content": "X"}
                    ],
                })
            },
        )()
        with patch.object(analyzer, "_invoke", new_callable=AsyncMock) as mock_invoke:
            mock_invoke.return_value = mock_response
            result = await analyzer.analyze_memory_update(
                user_message="用户认可这种结构",
                assistant_response="好的",
                memory_block="### 经验洞察\n(空)",
            )

        assert result.has_operations is True
        assert len(result.operations) == 1

    @pytest.mark.asyncio
    async def test_failure_returns_empty_result(
        self, analyzer: SimpleMemoryAnalyzer
    ) -> None:
        """LLM 调用失败时降级返回空结果, 不抛异常."""
        with patch.object(analyzer, "_invoke", new_callable=AsyncMock) as mock_invoke:
            mock_invoke.side_effect = RuntimeError("LLM down")
            result = await analyzer.analyze_memory_update(
                user_message="x", assistant_response="y", memory_block=""
            )

        assert result.has_operations is False
        assert result.operations == []
