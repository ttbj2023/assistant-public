"""健康数据提取→审计全生命周期集成测试.

验证健康数据从对话提取 → AsyncHealthDAO → SQLite → 审计快照读回 →
LLM 审计操作 → DB 更新的端到端闭环,
补充单元测试中 UnifiedHealthExtractor 与 AsyncHealthDAO、_call_audit_llm 各自被 Mock 的缺口.

测试策略: 灰盒 - 真实 HealthDataBackgroundExtractor / HealthDataExtractionService /
AsyncHealthDAO / SQLite, 仅 Mock 真正的外部依赖 (UnifiedHealthExtractor 与 _call_audit_llm).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.agents_implementations.health_assistant.health_data_audit import (
    clear_audit_state,
    load_data_snapshot,
    run_audit,
)
from src.agent.agents_implementations.health_assistant.health_data_background_extractor import (
    HealthDataBackgroundExtractor,
)
from src.inference.health_data_extraction.unified_extractor import (
    ExtractionResult,
    UnifiedHealthExtractor,
)
from src.storage.dao import async_database_manager as adm
from src.storage.service.health_data_extraction_service import (
    get_health_data_extraction_service,
)
from src.storage.service.service_factory import clear_vector_cache

_AGENT_ID = "test-health-assistant"


@pytest.fixture(autouse=True)
def _reset_health_state() -> Iterator[None]:
    """重置 DB 全局状态 + Service 缓存 + 审计状态, 避免跨事件循环污染."""
    adm._db_cache_lock = asyncio.Lock()
    adm._db_manager_cache.clear()
    clear_vector_cache()
    clear_audit_state()
    yield
    adm._db_cache_lock = asyncio.Lock()
    adm._db_manager_cache.clear()
    clear_vector_cache()
    clear_audit_state()


def _make_weight_result(weight: float = 70.5) -> ExtractionResult:
    """构造体重提取结果."""
    return ExtractionResult(
        data_type="weight_record",
        data={
            "weight": weight,
            "body_fat_percentage": 20.0,
            "muscle_mass": 30.0,
            "timestamp": datetime.now().isoformat(),
        },
    )


def _make_meal_result() -> ExtractionResult:
    """构造饮食提取结果."""
    return ExtractionResult(
        data_type="meal_record",
        data={
            "meal_type": "lunch",
            "meal_date": datetime.now().strftime("%Y-%m-%d"),
            "meal_time": "12:00",
            "items": [{"name": "米饭", "quantity": "1碗"}, {"name": "鸡肉"}],
            "notes": "微辣",
        },
    )


@pytest.mark.integration
class TestHealthDataPipelineIntegration:
    """健康数据提取→审计全生命周期集成测试."""

    @pytest.mark.asyncio
    async def test_extraction_writes_weight_record_to_db(
        self,
        test_user,
        test_thread_id,
    ):
        """user_message → extractor → store_extraction → DAO → SQLite.

        Mock 边界: UnifiedHealthExtractor.extract 返回固定体重结果
        验证重点: DB 可读回 / snapshot 含 [ID:1] 前缀
        """
        mock_extractor = MagicMock(spec=UnifiedHealthExtractor)
        mock_extractor.is_available.return_value = True
        mock_extractor.extract = AsyncMock(return_value=[_make_weight_result()])

        extractor = HealthDataBackgroundExtractor(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        with patch(
            "src.inference.health_data_extraction.unified_extractor.UnifiedHealthExtractor",
            return_value=mock_extractor,
        ):
            await extractor.extract_from_conversation("我体重70.5公斤", round_number=5)

        service = get_health_data_extraction_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        snapshot = await service.get_extraction_snapshot(min_round=1)
        assert "## weight_record" in snapshot
        assert "共 1 条" in snapshot
        assert "[ID:1]" in snapshot
        assert "70.5kg" in snapshot

    @pytest.mark.asyncio
    async def test_extraction_writes_multiple_data_types(
        self,
        test_user,
        test_thread_id,
    ):
        """饮食 + 体重混合提取, 各类型 DAO 分别可读回."""
        mock_extractor = MagicMock(spec=UnifiedHealthExtractor)
        mock_extractor.is_available.return_value = True
        mock_extractor.extract = AsyncMock(
            return_value=[_make_weight_result(), _make_meal_result()]
        )

        extractor = HealthDataBackgroundExtractor(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        with patch(
            "src.inference.health_data_extraction.unified_extractor.UnifiedHealthExtractor",
            return_value=mock_extractor,
        ):
            await extractor.extract_from_conversation(
                "我体重70.5公斤, 中午吃了米饭和鸡肉", round_number=3
            )

        service = get_health_data_extraction_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        snapshot = await service.get_extraction_snapshot(min_round=1)
        assert "## weight_record" in snapshot
        assert "## meal_record" in snapshot
        assert "70.5kg" in snapshot
        assert "米饭" in snapshot

    @pytest.mark.asyncio
    async def test_audit_snapshot_loads_real_data(
        self,
        test_user,
        test_thread_id,
    ):
        """预置数据到 AsyncHealthDAO → load_data_snapshot 真实 DB 读回含 [ID:1] 前缀."""
        service = get_health_data_extraction_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        await service.store_extraction(
            data_type="weight_record",
            data={"weight": 68.0},
            round_number=8,
        )

        snapshot = await load_data_snapshot(
            test_user, test_thread_id, _AGENT_ID, current_round=10
        )
        assert "## weight_record" in snapshot
        assert "[ID:1]" in snapshot
        assert "68.0kg" in snapshot

    @pytest.mark.asyncio
    async def test_audit_delete_operation_removes_record(
        self,
        test_user,
        test_thread_id,
    ):
        """预置两条记录 → run_audit 收到 delete operation → 最旧记录被删除.

        Mock 边界: _call_audit_llm 返回 delete 操作
        验证重点: 目标 ID 记录已删 / 剩余记录仍存在
        """
        service = get_health_data_extraction_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        result1 = await service.store_extraction(
            data_type="weight_record",
            data={"weight": 70.0},
            round_number=5,
        )
        result2 = await service.store_extraction(
            data_type="weight_record",
            data={"weight": 71.0},
            round_number=6,
        )
        first_id = result1["storage_result"]["id"]
        second_id = result2["storage_result"]["id"]

        with patch(
            "src.agent.agents_implementations.health_assistant.health_data_audit._call_audit_llm",
            AsyncMock(
                return_value={
                    "extractions": [],
                    "operations": [
                        {
                            "action": "delete",
                            "data_type": "weight_record",
                            "record_id": first_id,
                            "reason": "重复记录",
                        },
                    ],
                }
            ),
        ):
            await run_audit(test_user, test_thread_id, _AGENT_ID, current_round=10)

        dao = await service._get_dao()
        remaining = await dao._weight_record_ops.find_by_filters({}, limit=10)
        remaining_ids = {r.id for r in remaining}
        assert first_id not in remaining_ids
        assert second_id in remaining_ids

    @pytest.mark.asyncio
    async def test_extract_then_audit_end_to_end(
        self,
        test_user,
        test_thread_id,
    ):
        """round 5 提取 → round 10 审计, 审计 snapshot 含提取阶段写入的数据.

        Mock 边界: UnifiedHealthExtractor (提取) + _call_audit_llm (审计返回空操作)
        验证重点: _call_audit_llm 收到的 snapshot 包含 round 5 写入的体重记录
        """
        mock_extractor = MagicMock(spec=UnifiedHealthExtractor)
        mock_extractor.is_available.return_value = True
        mock_extractor.extract = AsyncMock(return_value=[_make_weight_result()])

        extractor = HealthDataBackgroundExtractor(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        with patch(
            "src.inference.health_data_extraction.unified_extractor.UnifiedHealthExtractor",
            return_value=mock_extractor,
        ):
            await extractor.extract_from_conversation("我体重70.5公斤", round_number=5)

        mock_audit_llm = AsyncMock(return_value={"extractions": [], "operations": []})
        with patch(
            "src.agent.agents_implementations.health_assistant.health_data_audit._call_audit_llm",
            mock_audit_llm,
        ):
            await run_audit(test_user, test_thread_id, _AGENT_ID, current_round=10)

        assert mock_audit_llm.called
        call_args = mock_audit_llm.call_args
        snapshot = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("data_snapshot", "")
        assert "## weight_record" in snapshot
        assert "70.5kg" in snapshot
        assert "R5" in snapshot
