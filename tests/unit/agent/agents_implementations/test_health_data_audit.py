"""健康数据审计任务单元测试.

覆盖 health_data_audit.py 的核心逻辑:
- 轮次驱动审计触发逻辑 (should_audit / mark_audited)
- 数据快照加载 (load_data_snapshot - Mock Service)
- 用户消息构建 (_build_message_text)
- JSON解析与验证 (_call_audit_llm - Mock create_llm)
- 数据过滤/清理/格式化已迁移到 HealthDataExtractionService, 见对应测试
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.agents_implementations.health_assistant.health_data_audit import (
    AUDIT_INTERVAL,
    _build_message_text,
    clear_audit_state,
    mark_audited,
    should_audit,
)


class TestShouldAudit:
    """审计触发条件测试."""

    def setup_method(self) -> None:
        clear_audit_state()

    def teardown_method(self) -> None:
        clear_audit_state()

    def test_first_audit_at_interval(self) -> None:
        assert should_audit("u", "t", "a", AUDIT_INTERVAL) is True

    def test_first_audit_below_interval(self) -> None:
        assert should_audit("u", "t", "a", 5) is False

    def test_within_interval_does_not_trigger(self) -> None:
        mark_audited("u", "t", "a", 10)
        assert should_audit("u", "t", "a", 15) is False

    def test_at_interval_triggers(self) -> None:
        mark_audited("u", "t", "a", 10)
        assert should_audit("u", "t", "a", 20) is True

    def test_different_keys_independent(self) -> None:
        mark_audited("u1", "t", "a", 10)
        assert should_audit("u2", "t", "a", AUDIT_INTERVAL) is True
        assert should_audit("u1", "t", "a", 15) is False


class TestMarkAudited:
    """审计轮次标记测试."""

    def setup_method(self) -> None:
        clear_audit_state()

    def teardown_method(self) -> None:
        clear_audit_state()

    def test_mark_and_check(self) -> None:
        mark_audited("u", "t", "a", 10)
        assert should_audit("u", "t", "a", 15) is False
        assert should_audit("u", "t", "a", 20) is True


class TestClearAuditState:
    """状态清理测试."""

    def test_clear_resets_state(self) -> None:
        mark_audited("u", "t", "a", 10)
        clear_audit_state()
        assert should_audit("u", "t", "a", AUDIT_INTERVAL) is True


class TestBuildMessageText:
    """用户消息构建测试."""

    def test_none_message_returns_empty(self) -> None:
        assert _build_message_text(None, None) == ""

    def test_basic_message(self) -> None:
        result = _build_message_text("我今天跑步了", None)
        assert "用户消息:" in result
        assert "我今天跑步了" in result

    def test_with_image_descriptions(self) -> None:
        infos = [
            SimpleNamespace(detail="一张食物照片, 有米饭和蔬菜"),
            SimpleNamespace(detail="图片"),
        ]
        result = _build_message_text("我吃了这个", infos)
        assert "[图片1描述]:" in result
        assert "食物照片" in result
        assert "[图片2描述]:" not in result

    def test_empty_attachment_list(self) -> None:
        result = _build_message_text("test", [])
        assert "图片" not in result


class TestFilterByRound:
    """轮次过滤测试."""

    def test_no_round_number_included(self) -> None:
        from src.storage.service.health_data_extraction_service import (
            HealthDataExtractionService,
        )

        records = [SimpleNamespace(round_number=None)]
        result = HealthDataExtractionService._filter_by_round(records, 5)
        assert len(result) == 1

    def test_recent_records_included(self) -> None:
        from src.storage.service.health_data_extraction_service import (
            HealthDataExtractionService,
        )

        records = [
            SimpleNamespace(round_number=8),
            SimpleNamespace(round_number=12),
        ]
        result = HealthDataExtractionService._filter_by_round(records, 5)
        assert len(result) == 2

    def test_old_records_excluded(self) -> None:
        from src.storage.service.health_data_extraction_service import (
            HealthDataExtractionService,
        )

        records = [
            SimpleNamespace(round_number=3),
            SimpleNamespace(round_number=8),
        ]
        result = HealthDataExtractionService._filter_by_round(records, 5)
        assert len(result) == 1
        assert result[0].round_number == 8

    def test_empty_list(self) -> None:
        from src.storage.service.health_data_extraction_service import (
            HealthDataExtractionService,
        )

        assert HealthDataExtractionService._filter_by_round([], 5) == []


class TestSanitizeUpdateData:
    """更新数据清理测试."""

    def test_removes_forbidden_keys(self) -> None:
        from src.storage.service.health_data_extraction_service import (
            HealthDataExtractionService,
        )

        data = {
            "id": 1,
            "created_at": "2025-01-01",
            "updated_at": "2025-01-02",
            "source": "test",
            "weight_kg": 70.5,
        }
        result = HealthDataExtractionService._sanitize_update_data(data)
        assert "id" not in result
        assert "created_at" not in result
        assert "updated_at" not in result
        assert "source" not in result
        assert result["weight_kg"] == pytest.approx(70.5)

    def test_empty_data(self) -> None:
        from src.storage.service.health_data_extraction_service import (
            HealthDataExtractionService,
        )

        assert HealthDataExtractionService._sanitize_update_data({}) == {}


class TestFormatMealItems:
    """饮食记录格式化测试."""

    def test_none_items(self) -> None:
        from src.storage.service.health_data_extraction_service import (
            _format_meal_items,
        )

        assert _format_meal_items(None) == "无详情"

    def test_json_string_items(self) -> None:
        from src.storage.service.health_data_extraction_service import (
            _format_meal_items,
        )

        items = json.dumps([
            {"name": "米饭", "quantity": "1碗"},
            {"name": "蔬菜", "quantity": "2份"},
        ])
        result = _format_meal_items(items)
        assert "米饭(1碗)" in result
        assert "蔬菜(2份)" in result

    def test_list_items(self) -> None:
        from src.storage.service.health_data_extraction_service import (
            _format_meal_items,
        )

        items = [{"name": "鸡肉"}, {"name": "牛奶", "quantity": "250ml"}]
        result = _format_meal_items(items)
        assert "鸡肉" in result
        assert "牛奶(250ml)" in result

    def test_many_items_truncation(self) -> None:
        from src.storage.service.health_data_extraction_service import (
            _format_meal_items,
        )

        items = [{"name": f"食物{i}"} for i in range(10)]
        result = _format_meal_items(items)
        assert "等10项" in result

    def test_invalid_json_fallback(self) -> None:
        from src.storage.service.health_data_extraction_service import (
            _format_meal_items,
        )

        result = _format_meal_items("not valid json{{{")
        assert len(result) <= 80


class TestLoadDataSnapshot:
    """数据快照加载测试 (Mock Service层)."""

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty(self) -> None:
        from src.agent.agents_implementations.health_assistant.health_data_audit import (
            load_data_snapshot,
        )

        mock_service = AsyncMock()
        mock_service.get_extraction_snapshot.return_value = ""

        with patch(
            "src.agent.agents_implementations.health_assistant.health_data_audit"
            ".get_health_data_extraction_service",
            return_value=mock_service,
        ):
            result = await load_data_snapshot("u", "t", "a", 5)
            assert result == ""

    @pytest.mark.asyncio
    async def test_with_weight_records(self) -> None:
        from src.agent.agents_implementations.health_assistant.health_data_audit import (
            load_data_snapshot,
        )

        snapshot_text = "## weight_records\n共 1 条\n[ID:1] 70.5kg, R3"
        mock_service = AsyncMock()
        mock_service.get_extraction_snapshot.return_value = snapshot_text

        with patch(
            "src.agent.agents_implementations.health_assistant.health_data_audit"
            ".get_health_data_extraction_service",
            return_value=mock_service,
        ):
            result = await load_data_snapshot("u", "t", "a", 5)
            assert "weight_records" in result
            assert "70.5kg" in result
            assert "[ID:1]" in result


class TestCallAuditLlm:
    """审计LLM调用测试 (Mock create_llm)."""

    @staticmethod
    def _make_mock_llm(content: str):
        mock_response = AsyncMock(content=content)
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm.bind = MagicMock(return_value=mock_llm)
        return mock_llm

    @pytest.mark.asyncio
    async def test_valid_response_parsing(self) -> None:
        from src.agent.agents_implementations.health_assistant.health_data_audit import (
            _call_audit_llm,
        )

        llm_response = {
            "extractions": [
                {
                    "data_type": "weight_record",
                    "data": {"weight_kg": 70.5},
                }
            ],
            "operations": [
                {
                    "action": "delete",
                    "data_type": "meal_record",
                    "record_id": 42,
                    "reason": "重复记录",
                }
            ],
        }

        with (
            patch(
                "src.agent.agents_implementations.health_assistant.health_data_audit"
                "._load_audit_prompt",
                return_value="{user_message}\n{data_snapshot}\n{current_date}",
            ),
            patch(
                "src.agent.agents_implementations.health_assistant.health_data_audit"
                "._get_model_id",
                return_value="deepseek:deepseek-v4-flash",
            ),
            patch(
                "src.inference.llm.model_loader.create_llm",
                return_value=self._make_mock_llm(
                    json.dumps(llm_response, ensure_ascii=False)
                ),
            ),
        ):
            result = await _call_audit_llm("测试消息", "测试快照")

            assert len(result["extractions"]) == 1
            assert result["extractions"][0]["data_type"] == "weight_record"
            assert len(result["operations"]) == 1
            assert result["operations"][0]["action"] == "delete"

    @pytest.mark.asyncio
    async def test_empty_response(self) -> None:
        from src.agent.agents_implementations.health_assistant.health_data_audit import (
            _call_audit_llm,
        )

        mock_response_obj = AsyncMock(content="")
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response_obj)
        mock_llm.bind = MagicMock(return_value=mock_llm)

        with (
            patch(
                "src.agent.agents_implementations.health_assistant.health_data_audit"
                "._load_audit_prompt",
                return_value="prompt",
            ),
            patch(
                "src.agent.agents_implementations.health_assistant.health_data_audit"
                "._get_model_id",
                return_value="deepseek:deepseek-v4-flash",
            ),
            patch(
                "src.inference.llm.model_loader.create_llm",
                return_value=mock_llm,
            ),
        ):
            result = await _call_audit_llm("msg", "snap")
            assert result == {"extractions": [], "operations": []}

    @pytest.mark.asyncio
    async def test_invalid_data_type_filtered(self) -> None:
        from src.agent.agents_implementations.health_assistant.health_data_audit import (
            _call_audit_llm,
        )

        llm_response = {
            "extractions": [
                {"data_type": "invalid_type", "data": {"x": 1}},
                {"data_type": "weight_record", "data": {"weight_kg": 65}},
            ],
            "operations": [],
        }

        with (
            patch(
                "src.agent.agents_implementations.health_assistant.health_data_audit"
                "._load_audit_prompt",
                return_value="prompt",
            ),
            patch(
                "src.agent.agents_implementations.health_assistant.health_data_audit"
                "._get_model_id",
                return_value="deepseek:deepseek-v4-flash",
            ),
            patch(
                "src.inference.llm.model_loader.create_llm",
                return_value=self._make_mock_llm(json.dumps(llm_response)),
            ),
        ):
            result = await _call_audit_llm("msg", "snap")
            assert len(result["extractions"]) == 1
            assert result["extractions"][0]["data_type"] == "weight_record"

    @pytest.mark.asyncio
    async def test_invalid_operations_filtered(self) -> None:
        from src.agent.agents_implementations.health_assistant.health_data_audit import (
            _call_audit_llm,
        )

        llm_response = {
            "extractions": [],
            "operations": [
                {"action": "update", "data_type": "meal_record", "record_id": None},
                {"action": "create", "data_type": "weight_record"},
                {"action": "delete", "data_type": "invalid", "record_id": 1},
            ],
        }

        with (
            patch(
                "src.agent.agents_implementations.health_assistant.health_data_audit"
                "._load_audit_prompt",
                return_value="prompt",
            ),
            patch(
                "src.agent.agents_implementations.health_assistant.health_data_audit"
                "._get_model_id",
                return_value="deepseek:deepseek-v4-flash",
            ),
            patch(
                "src.inference.llm.model_loader.create_llm",
                return_value=self._make_mock_llm(json.dumps(llm_response)),
            ),
        ):
            result = await _call_audit_llm("msg", "snap")
            assert len(result["operations"]) == 0


class TestRunAudit:
    """run_audit 集成测试 (Mock所有外部依赖)."""

    @pytest.mark.asyncio
    async def test_no_data_skips(self) -> None:
        from src.agent.agents_implementations.health_assistant.health_data_audit import (
            run_audit,
        )

        clear_audit_state()

        with (
            patch(
                "src.agent.agents_implementations.health_assistant.health_data_audit"
                ".load_data_snapshot",
                new_callable=AsyncMock,
                return_value="",
            ),
        ):
            await run_audit("u", "t", "a", 10, user_message=None)
            assert should_audit("u", "t", "a", 15) is False

    @pytest.mark.asyncio
    async def test_successful_audit_marks_round(self) -> None:
        from src.agent.agents_implementations.health_assistant.health_data_audit import (
            run_audit,
        )

        clear_audit_state()

        with (
            patch(
                "src.agent.agents_implementations.health_assistant.health_data_audit"
                ".load_data_snapshot",
                new_callable=AsyncMock,
                return_value="## weight_records\n1条",
            ),
            patch(
                "src.agent.agents_implementations.health_assistant.health_data_audit"
                "._call_audit_llm",
                new_callable=AsyncMock,
                return_value={"extractions": [], "operations": []},
            ),
        ):
            await run_audit("u", "t", "a", 10, user_message="测试")
            assert should_audit("u", "t", "a", 15) is False

    @pytest.mark.asyncio
    async def test_exception_still_marks_round(self) -> None:
        from src.agent.agents_implementations.health_assistant.health_data_audit import (
            run_audit,
        )

        clear_audit_state()

        with (
            patch(
                "src.agent.agents_implementations.health_assistant.health_data_audit"
                ".load_data_snapshot",
                new_callable=AsyncMock,
                side_effect=Exception("DB error"),
            ),
        ):
            await run_audit("u", "t", "a", 10, user_message="test")
            assert should_audit("u", "t", "a", 15) is False
