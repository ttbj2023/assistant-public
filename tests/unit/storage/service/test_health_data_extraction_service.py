"""HealthDataExtractionService单元测试.

测试健康数据提取服务的业务逻辑:
- 数据预处理 (kJ<->kcal 机械转换)
- 数据类型分发存储 (6种数据类型 + 未知类型)
- 审计快照加载与格式化
- 审计操作执行 (create/update/delete)
- 纯函数格式化器

遵循单元测试设计规范: Mock DAO(AsyncHealthDAO), 保留真实业务逻辑.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.storage.service.health_data_extraction_service import (
    HealthDataExtractionService,
    _fmt_meal,
    _fmt_product,
    _fmt_report,
    _fmt_shopping,
    _fmt_weight,
    _fmt_workout,
    _format_meal_items,
)


@pytest.fixture
def service():
    """创建服务实例(不触发DAO初始化)."""
    return HealthDataExtractionService(
        "test_user", "test_thread", agent_id="health_assistant"
    )


@pytest.fixture
def mock_dao():
    """模拟 AsyncHealthDAO, 预置全部审计 ops 属性."""
    dao = AsyncMock()
    for ops_attr in HealthDataExtractionService._AUDIT_DATA_TYPES:
        ops = AsyncMock()
        ops.find_by_filters = AsyncMock(return_value=[])
        ops.delete_by_id = AsyncMock(return_value=True)
        ops.update = AsyncMock(return_value=True)
        setattr(dao, ops_attr, ops)
    return dao


class TestPreprocess:
    """_preprocess 的 kJ<->kcal 机械转换."""

    def test_food_product_kj_to_kcal_when_no_calories(self, service):
        """有 energy_kj 但无 calories 时按 4.184 换算出 calories."""
        data = {"nutrition_per_100g": {"energy_kj": 418.4}}
        result = service._preprocess("food_product", data)
        assert result["nutrition_per_100g"]["calories"] == 100.0

    def test_food_product_kcal_to_kj_when_no_kj(self, service):
        """有 calories 但无 energy_kj 时按 4.184 反算 energy_kj."""
        data = {"nutrition_per_100g": {"calories": 100.0}}
        result = service._preprocess("food_product", data)
        assert result["nutrition_per_100g"]["energy_kj"] == round(100.0 * 4.184, 1)

    def test_food_product_no_conversion_when_both_present(self, service):
        """energy_kj 与 calories 都存在时不做转换."""
        data = {"nutrition_per_100g": {"energy_kj": 418.4, "calories": 100.0}}
        result = service._preprocess("food_product", data)
        assert result["nutrition_per_100g"] == {"energy_kj": 418.4, "calories": 100.0}

    def test_food_product_passthrough_when_nutrition_not_dict(self, service):
        """nutrition_per_100g 非 dict 时原样返回."""
        data = {"nutrition_per_100g": None}
        result = service._preprocess("food_product", data)
        assert result == data

    def test_non_food_product_should_passthrough(self, service):
        """非 food_product 类型不进入预处理逻辑."""
        data = {"weight": 70.0}
        result = service._preprocess("weight_record", data)
        assert result == data


class TestStoreExtractionDispatch:
    """store_extraction 对各数据类型的字段映射与存储."""

    @pytest.mark.asyncio
    async def test_store_weight_record_should_map_fields(self, service, mock_dao):
        """体重记录应将 weight 映射为 weight_kg 并附 source."""
        mock_dao.create_weight_record = AsyncMock(
            return_value=SimpleNamespace(id=1, weight_kg=70.0)
        )
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.store_extraction(
                "weight_record",
                {"weight": 70.0, "timestamp": "2026-06-01T08:00:00"},
            )

        assert result["success"] is True
        assert result["data_type_label"] == "体重记录"
        assert result["storage_result"] == {"id": 1, "weight": 70.0}
        call = mock_dao.create_weight_record.call_args[0][0]
        assert call["weight_kg"] == 70.0
        assert call["recorded_at"] == datetime(2026, 6, 1, 8, 0, 0)
        assert call["source"] == "conversation_extraction"

    @pytest.mark.asyncio
    async def test_store_workout_record_should_fallback_duration(
        self, service, mock_dao
    ):
        """运动记录应优先 duration_minutes, 缺失时回退 duration."""
        mock_dao.create_workout_record = AsyncMock(
            return_value=SimpleNamespace(id=2, workout_type="Running")
        )
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.store_extraction(
                "workout_record",
                {"workout_type": "Running", "duration_minutes": 30},
            )

        assert result["storage_result"] == {"id": 2, "workout_type": "Running"}
        call = mock_dao.create_workout_record.call_args[0][0]
        assert call["duration"] == 30

    @pytest.mark.asyncio
    async def test_store_meal_record_should_parse_meal_date(self, service, mock_dao):
        """饮食记录应将 meal_date 字符串解析为 date."""
        mock_dao.create_meal_record = AsyncMock(
            return_value=SimpleNamespace(id=3, meal_type="lunch")
        )
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.store_extraction(
                "meal_record",
                {"meal_type": "lunch", "meal_date": "2026-06-01", "items": []},
            )

        assert result["storage_result"] == {"id": 3, "meal_type": "lunch"}
        call = mock_dao.create_meal_record.call_args[0][0]
        assert call["meal_date"].isoformat() == "2026-06-01"

    @pytest.mark.asyncio
    async def test_store_shopping_list_should_handle_single_item(
        self, service, mock_dao
    ):
        """shopping_list 的 items 非列表时应包装为单元素列表."""
        mock_dao.create_shopping_item = AsyncMock(
            return_value=SimpleNamespace(id=4, name="牛奶")
        )
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.store_extraction(
                "shopping_list", {"items": {"name": "牛奶", "quantity": 2}}
            )

        assert result["storage_result"]["items"] == [{"id": 4, "name": "牛奶"}]
        mock_dao.create_shopping_item.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_food_product_should_pass_preprocessed_data(
        self, service, mock_dao
    ):
        """食品目录应经预处理后存储."""
        mock_dao.create_food_product = AsyncMock(
            return_value=SimpleNamespace(id=5, name="燕麦")
        )
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.store_extraction(
                "food_product",
                {
                    "product_id": "P1",
                    "name": "燕麦",
                    "nutrition_per_100g": {"energy_kj": 418.4},
                },
            )

        assert result["storage_result"] == {"id": 5, "name": "燕麦"}
        call = mock_dao.create_food_product.call_args[0][0]
        # 预处理应已补出 calories
        assert call["nutrition_per_100g"]["calories"] == 100.0

    @pytest.mark.asyncio
    async def test_store_medical_report_should_map_fields(self, service, mock_dao):
        """体检报告应映射 report_date/report_data/report_type."""
        mock_dao.save_medical_report = AsyncMock(return_value=SimpleNamespace(id=6))
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.store_extraction(
                "medical_report",
                {"report_date": "2026-06-01", "report_data": {"bp": "120/80"}},
            )

        assert result["storage_result"] == {"id": 6}
        call = mock_dao.save_medical_report.call_args[0][0]
        assert call["report_type"] == "routine"

    @pytest.mark.asyncio
    async def test_store_unknown_data_type_should_return_warning(
        self, service, mock_dao
    ):
        """未知数据类型应返回 warning 而非抛错."""
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.store_extraction("unknown_type", {})

        assert result["success"] is True
        assert result["storage_result"]["warning"].startswith("未知的数据类型")

    @pytest.mark.asyncio
    async def test_store_should_return_error_on_exception(self, service):
        """DAO 初始化异常时应返回 success=False."""
        with patch.object(
            service, "_get_dao", AsyncMock(side_effect=Exception("db down"))
        ):
            result = await service.store_extraction("weight_record", {"weight": 70.0})

        assert result["success"] is False
        assert "db down" in result["error"]
        assert result["data_type"] == "weight_record"


class TestGetExtractionSnapshot:
    """get_extraction_snapshot 的快照加载."""

    @pytest.mark.asyncio
    async def test_should_return_empty_when_no_records(self, service, mock_dao):
        """所有类型均无记录时应返回空字符串."""
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.get_extraction_snapshot(min_round=1)

        assert result == ""

    @pytest.mark.asyncio
    async def test_should_format_weight_section(self, service, mock_dao):
        """有体重记录时应输出对应快照段落."""
        record = SimpleNamespace(
            id=1,
            weight_kg=70.0,
            body_fat_pct=20.0,
            muscle_mass_kg=30.0,
            recorded_at=datetime(2026, 6, 1, 8, 0),
            round_number=5,
        )
        mock_dao._weight_record_ops.find_by_filters = AsyncMock(return_value=[record])
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.get_extraction_snapshot(min_round=1)

        assert "weight_record" in result
        assert "70.0kg" in result
        assert "体脂:20.0%" in result

    @pytest.mark.asyncio
    async def test_should_filter_out_low_round_records(self, service, mock_dao):
        """round_number 低于 min_round 的记录应被过滤."""
        low = SimpleNamespace(
            id=1,
            weight_kg=70.0,
            body_fat_pct=None,
            muscle_mass_kg=None,
            recorded_at=None,
            round_number=1,
        )
        ok = SimpleNamespace(
            id=2,
            weight_kg=71.0,
            body_fat_pct=None,
            muscle_mass_kg=None,
            recorded_at=None,
            round_number=10,
        )
        mock_dao._weight_record_ops.find_by_filters = AsyncMock(return_value=[low, ok])
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.get_extraction_snapshot(min_round=5)

        assert "71.0kg" in result
        assert "70.0kg" not in result

    @pytest.mark.asyncio
    async def test_should_return_empty_on_exception(self, service, mock_dao):
        """加载异常时应降级返回空字符串."""
        with patch.object(
            service, "_get_dao", AsyncMock(side_effect=Exception("fail"))
        ):
            result = await service.get_extraction_snapshot(min_round=1)

        assert result == ""


class TestExecuteAuditOperation:
    """execute_audit_operation 的 create/update/delete 分支."""

    @pytest.mark.asyncio
    async def test_create_without_data_should_return_error(self, service, mock_dao):
        """create 操作缺少 data 时应返回 success=False."""
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.execute_audit_operation(
                "create", "weight_record", data=None
            )

        assert result["success"] is False
        assert "data" in result["error"]

    @pytest.mark.asyncio
    async def test_create_should_delegate_to_store_extraction(self, service, mock_dao):
        """create 操作应委托给 store_extraction."""
        mock_dao.create_weight_record = AsyncMock(
            return_value=SimpleNamespace(id=1, weight_kg=70.0)
        )
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.execute_audit_operation(
                "create", "weight_record", data={"weight": 70.0}
            )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_unknown_data_type_should_return_error(self, service, mock_dao):
        """未知数据类型的 update/delete 应返回 success=False."""
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.execute_audit_operation(
                "delete", "unknown_type", record_id=1
            )

        assert result["success"] is False
        assert "未知数据类型" in result["error"]

    @pytest.mark.asyncio
    async def test_delete_without_record_id_should_return_error(
        self, service, mock_dao
    ):
        """delete 操作缺少 record_id 时应返回 success=False."""
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.execute_audit_operation(
                "delete", "weight_record", record_id=None
            )

        assert result["success"] is False
        assert "record_id" in result["error"]

    @pytest.mark.asyncio
    async def test_delete_success_should_return_action(self, service, mock_dao):
        """delete 成功应返回 action=delete."""
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.execute_audit_operation(
                "delete", "weight_record", record_id=5
            )

        assert result == {"success": True, "action": "delete", "record_id": 5}

    @pytest.mark.asyncio
    async def test_delete_nonexistent_should_return_error(self, service, mock_dao):
        """delete 命中不存在的记录应返回 success=False."""
        mock_dao._weight_record_ops.delete_by_id = AsyncMock(return_value=False)
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.execute_audit_operation(
                "delete", "weight_record", record_id=99
            )

        assert result["success"] is False
        assert "记录不存在" in result["error"]

    @pytest.mark.asyncio
    async def test_update_without_record_id_should_return_error(
        self, service, mock_dao
    ):
        """update 操作缺少 record_id 时应返回 success=False."""
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.execute_audit_operation(
                "update", "weight_record", data={"weight": 71.0}
            )

        assert result["success"] is False
        assert "record_id" in result["error"]

    @pytest.mark.asyncio
    async def test_update_without_data_should_return_error(self, service, mock_dao):
        """update 操作缺少 data 时应返回 success=False."""
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.execute_audit_operation(
                "update", "weight_record", record_id=1, data=None
            )

        assert result["success"] is False
        assert "data" in result["error"]

    @pytest.mark.asyncio
    async def test_update_success_should_sanitize_and_delegate(self, service, mock_dao):
        """update 应清理禁止字段后委托给 ops.update."""
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.execute_audit_operation(
                "update",
                "weight_record",
                record_id=1,
                data={"weight": 71.0, "id": 999, "source": "evil"},
            )

        assert result == {"success": True, "action": "update", "record_id": 1}
        clean = mock_dao._weight_record_ops.update.call_args[0][1]
        assert "id" not in clean
        assert "source" not in clean
        assert clean["weight"] == 71.0

    @pytest.mark.asyncio
    async def test_update_nonexistent_should_return_error(self, service, mock_dao):
        """update 命中不存在的记录应返回 success=False."""
        mock_dao._weight_record_ops.update = AsyncMock(return_value=None)
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.execute_audit_operation(
                "update", "weight_record", record_id=99, data={"weight": 71.0}
            )

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_unknown_action_should_return_error(self, service, mock_dao):
        """未知操作类型应返回 success=False."""
        with patch.object(service, "_get_dao", AsyncMock(return_value=mock_dao)):
            result = await service.execute_audit_operation(
                "merge", "weight_record", record_id=1, data={"weight": 71.0}
            )

        assert result["success"] is False
        assert "未知操作类型" in result["error"]


class TestPureHelpers:
    """纯函数辅助方法."""

    def test_filter_by_round_should_keep_none_and_above_min(self, service):
        """round_number 为 None 或 >= min_round 的记录应保留."""
        records = [
            SimpleNamespace(id=1, round_number=None),
            SimpleNamespace(id=2, round_number=3),
            SimpleNamespace(id=3, round_number=10),
        ]
        result = service._filter_by_round(records, min_round=5)
        assert [r.id for r in result] == [1, 3]

    def test_sanitize_update_data_should_remove_forbidden_keys(self, service):
        """应移除 id/created_at/updated_at/source 等元数据字段."""
        data = {
            "id": 1,
            "created_at": "x",
            "updated_at": "y",
            "source": "z",
            "weight": 70.0,
        }
        result = service._sanitize_update_data(data)
        assert result == {"weight": 70.0}

    def test_format_snapshot_section_should_return_empty_when_no_records(self, service):
        assert service._format_snapshot_section("weight_record", []) == ""

    def test_format_snapshot_section_should_return_empty_when_no_formatter(
        self, service
    ):
        """无对应格式化器的类型应返回空."""
        record = SimpleNamespace(id=1)
        assert service._format_snapshot_section("unknown", [record]) == ""


class TestFormatters:
    """快照格式化器纯函数."""

    def test_fmt_weight_full(self):
        """体重格式化应包含全部可选字段."""
        w = SimpleNamespace(
            id=1,
            weight_kg=70.0,
            body_fat_pct=20.0,
            muscle_mass_kg=30.0,
            recorded_at=datetime(2026, 6, 1, 8, 0),
            round_number=5,
        )
        result = _fmt_weight(w)
        assert result == "[ID:1] 70.0kg, 体脂:20.0%, 肌肉:30.0kg, 2026-06-01 08:00, R5"

    def test_fmt_weight_minimal(self):
        """仅体重时格式化应只含体重."""
        w = SimpleNamespace(
            id=1,
            weight_kg=70.0,
            body_fat_pct=None,
            muscle_mass_kg=None,
            recorded_at=None,
            round_number=None,
        )
        assert _fmt_weight(w) == "[ID:1] 70.0kg"

    def test_fmt_meal_with_items(self):
        m = SimpleNamespace(
            id=1,
            meal_date="2026-06-01",
            meal_type="lunch",
            meal_time="12:00",
            notes="微辣",
            round_number=3,
            items=[{"name": "米饭", "quantity": 1}, {"name": "鸡肉"}],
        )
        result = _fmt_meal(m)
        assert "[ID:1] 2026-06-01 lunch 12:00 R3:" in result
        assert "米饭(1)" in result
        assert "鸡肉" in result
        assert "(微辣)" in result

    def test_fmt_meal_unknown_type(self):
        """meal_type 为 None 时应显示未知餐型."""
        m = SimpleNamespace(
            id=1,
            meal_date="2026-06-01",
            meal_type=None,
            meal_time=None,
            notes=None,
            round_number=None,
            items=[],
        )
        result = _fmt_meal(m)
        assert "未知餐型" in result

    def test_fmt_workout_full(self):
        w = SimpleNamespace(
            id=1,
            workout_type="Running",
            duration=30.0,
            distance=5.0,
            start_time=datetime(2026, 6, 1, 8, 0),
            round_number=2,
            notes="轻松",
        )
        result = _fmt_workout(w)
        assert (
            result == "[ID:1] Running, 30.0分钟, 5.0km, 2026-06-01 08:00, R2, 备注:轻松"
        )

    def test_fmt_shopping_full(self):
        s = SimpleNamespace(
            id=1,
            name="牛奶",
            quantity=2,
            purchase_date=datetime(2026, 6, 1).date(),
            round_number=1,
        )
        result = _fmt_shopping(s)
        assert result == "[ID:1] 牛奶 x2, 2026-06-01 R1"

    def test_fmt_product_with_brand(self):
        p = SimpleNamespace(
            id=1,
            name="燕麦",
            brand="桂格",
            round_number=1,
            nutrition_per_100g={"calories": 100.0},
        )
        result = _fmt_product(p)
        assert "[ID:1] 燕麦 (桂格)" in result
        assert "100.0" in result

    def test_fmt_report_with_data(self):
        r = SimpleNamespace(
            id=1,
            report_date=datetime(2026, 6, 1),
            report_type="routine",
            round_number=1,
            report_data={"bp": "120/80"},
        )
        result = _fmt_report(r)
        assert "[ID:1] 2026-06-01 (routine)" in result
        assert "120/80" in result

    def test_fmt_report_empty_data(self):
        """report_data 为空时应显示 {}."""
        r = SimpleNamespace(
            id=1,
            report_date=datetime(2026, 6, 1),
            report_type=None,
            round_number=None,
            report_data={},
        )
        result = _fmt_report(r)
        assert result.endswith(": {}")
        assert "未知" in result


class TestFormatMealItems:
    """_format_meal_items 的多种输入处理."""

    def test_empty_items_should_return_placeholder(self):
        assert _format_meal_items([]) == "无详情"
        assert _format_meal_items(None) == "无详情"

    def test_list_items_should_format(self):
        items = [{"name": "米饭", "quantity": 1}, {"name": "鸡肉"}]
        result = _format_meal_items(items)
        assert "米饭(1)" in result
        assert "鸡肉" in result

    def test_items_over_six_should_append_count(self):
        """超过6项时应追加"等N项"."""
        items = [{"name": f"食物{i}"} for i in range(8)]
        result = _format_meal_items(items)
        assert "等8项" in result

    def test_json_string_items_should_be_parsed(self):
        """字符串形式的 JSON items 应被解析."""
        import json

        items = json.dumps([{"name": "米饭", "quantity": 2}])
        result = _format_meal_items(items)
        assert "米饭(2)" in result

    def test_invalid_items_should_fallback_to_str(self):
        """格式异常时应降级为 str 截断."""
        # 非 dict 元素触发 it.get 失败
        result = _format_meal_items([1, 2, 3])
        assert isinstance(result, str)
