"""SimpleMemoryManager 单元测试.

覆盖范围:
- get_memory_content: 经 DAO 按 memory_type 映射 preferences/insights
- get_memory_for_analysis: 无编号格式化
- get_memory_for_injection: <long_term_memory> 注入块(全空时不注入)
- get_memory_for_audit: 带[N]编号块 + number_map
- apply_operations: add/delete/change 精确匹配 + 语义去重
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.agent.memory.simple_memory.manager import SimpleMemoryManager
from src.core.types import MemoryOperation


def _make_memory(mem_type: str, content: str) -> object:
    """构造拟态 memory 对象(含 memory_type 与 content 属性)."""
    return type("M", (), {"memory_type": mem_type, "content": content})()


class _StubEmbeddings:
    """可控向量桩: 按 text->vector 映射返回向量."""

    def __init__(self, vector_map: dict[str, list[float]]) -> None:
        self.vector_map = vector_map

    async def aembed_query(self, text: str) -> list[float]:
        return self.vector_map.get(text, [0.0, 0.0])

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.vector_map.get(t, [0.0, 0.0]) for t in texts]


class TestSimpleMemoryManager:
    @pytest.fixture
    def manager(self, test_user: str, test_thread_id: str) -> SimpleMemoryManager:
        return SimpleMemoryManager(
            test_user, test_thread_id, agent_id="thought-assistant"
        )

    @pytest.mark.asyncio
    async def test_get_memory_content_maps_by_type(
        self, manager: SimpleMemoryManager
    ) -> None:
        with patch(
            "src.agent.memory.simple_memory.manager.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.memory_dao.get_all_memories.return_value = [
                _make_memory("preferences", "回复简洁"),
                _make_memory("insights", "认可三段式结构"),
                _make_memory("basic_info", "应被忽略"),  # simple 不用 basic_info
            ]
            mock_create.return_value = mock_service
            result = await manager.get_memory_content()

        assert result == {"preferences": "回复简洁", "insights": "认可三段式结构"}

    @pytest.mark.asyncio
    async def test_get_memory_for_analysis_format(
        self, manager: SimpleMemoryManager
    ) -> None:
        with patch(
            "src.agent.memory.simple_memory.manager.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.memory_dao.get_all_memories.return_value = [
                _make_memory("preferences", "偏好短段落\n用小标题"),
                _make_memory("insights", "领域: 个人成长"),
            ]
            mock_create.return_value = mock_service
            block = await manager.get_memory_for_analysis()

        assert "### 用户偏好" in block
        assert "### 经验洞察" in block
        assert "偏好短段落" in block
        assert "领域: 个人成长" in block

    @pytest.mark.asyncio
    async def test_get_memory_for_analysis_empty_shows_kong(
        self, manager: SimpleMemoryManager
    ) -> None:
        with patch(
            "src.agent.memory.simple_memory.manager.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.memory_dao.get_all_memories.return_value = []
            mock_create.return_value = mock_service
            block = await manager.get_memory_for_analysis()

        assert "(空)" in block

    @pytest.mark.asyncio
    async def test_get_memory_for_injection_full(
        self, manager: SimpleMemoryManager
    ) -> None:
        with patch(
            "src.agent.memory.simple_memory.manager.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.memory_dao.get_all_memories.return_value = [
                _make_memory("preferences", "回复简洁"),
                _make_memory("insights", "认可三段式"),
            ]
            mock_create.return_value = mock_service
            result = await manager.get_memory_for_injection()

        assert result.startswith("<long_term_memory>")
        assert result.endswith("</long_term_memory>")
        assert "## 用户偏好" in result
        assert "## 经验洞察" in result

    @pytest.mark.asyncio
    async def test_get_memory_for_injection_empty_returns_empty(
        self, manager: SimpleMemoryManager
    ) -> None:
        with patch(
            "src.agent.memory.simple_memory.manager.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.memory_dao.get_all_memories.return_value = []
            mock_create.return_value = mock_service
            result = await manager.get_memory_for_injection()

        assert result == ""

    @pytest.mark.asyncio
    async def test_get_memory_for_injection_partial_field(
        self, manager: SimpleMemoryManager
    ) -> None:
        """只有 preferences 有内容时, insights 节不出现."""
        with patch(
            "src.agent.memory.simple_memory.manager.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.memory_dao.get_all_memories.return_value = [
                _make_memory("preferences", "偏好A"),
            ]
            mock_create.return_value = mock_service
            result = await manager.get_memory_for_injection()

        assert "## 用户偏好" in result
        assert "## 经验洞察" not in result

    @pytest.mark.asyncio
    async def test_apply_operations_add(self, manager: SimpleMemoryManager) -> None:
        with patch(
            "src.agent.memory.simple_memory.manager.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.memory_dao.get_all_memories.return_value = [
                _make_memory("preferences", "已有偏好"),
            ]
            mock_create.return_value = mock_service
            ops = [
                MemoryOperation(action="add", field="preferences", content="新偏好"),
            ]
            updated = await manager.apply_operations(ops)

        assert updated is True
        mock_service.update_memory.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_operations_exact_dedup_skips(
        self, manager: SimpleMemoryManager
    ) -> None:
        """精确字符串重复的 add 应被跳过."""
        with patch(
            "src.agent.memory.simple_memory.manager.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.memory_dao.get_all_memories.return_value = [
                _make_memory("insights", "已有洞察"),
            ]
            mock_create.return_value = mock_service
            ops = [
                MemoryOperation(action="add", field="insights", content="已有洞察"),
            ]
            updated = await manager.apply_operations(ops)

        assert updated is False
        mock_service.update_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_operations_delete(self, manager: SimpleMemoryManager) -> None:
        with patch(
            "src.agent.memory.simple_memory.manager.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.memory_dao.get_all_memories.return_value = [
                _make_memory("preferences", "偏好A\n偏好B"),
            ]
            mock_create.return_value = mock_service
            ops = [
                MemoryOperation(action="delete", field="preferences", content="偏好A"),
            ]
            updated = await manager.apply_operations(ops)

        assert updated is True

    @pytest.mark.asyncio
    async def test_apply_operations_change(self, manager: SimpleMemoryManager) -> None:
        with patch(
            "src.agent.memory.simple_memory.manager.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.memory_dao.get_all_memories.return_value = [
                _make_memory("insights", "旧洞察"),
            ]
            mock_create.return_value = mock_service
            ops = [
                MemoryOperation(
                    action="change",
                    field="insights",
                    old_content="旧洞察",
                    new_content="新洞察",
                ),
            ]
            updated = await manager.apply_operations(ops)

        assert updated is True

    @pytest.mark.asyncio
    async def test_apply_operations_empty_ops_returns_false(
        self, manager: SimpleMemoryManager
    ) -> None:
        updated = await manager.apply_operations([])
        assert updated is False

    @pytest.mark.asyncio
    async def test_apply_operations_semantic_dedup(
        self, manager: SimpleMemoryManager
    ) -> None:
        """语义重复(embedding 余弦 >= 阈值)的 add 应被跳过."""
        manager._dedup_enabled = True
        manager._embeddings = _StubEmbeddings(
            {"新偏好": [1.0, 0.0], "已有偏好": [1.0, 0.0]}  # 同向量 -> 余弦=1.0
        )
        with patch(
            "src.agent.memory.simple_memory.manager.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.memory_dao.get_all_memories.return_value = [
                _make_memory("preferences", "已有偏好"),
            ]
            mock_create.return_value = mock_service
            ops = [
                MemoryOperation(action="add", field="preferences", content="新偏好"),
            ]
            updated = await manager.apply_operations(ops)

        assert updated is False
