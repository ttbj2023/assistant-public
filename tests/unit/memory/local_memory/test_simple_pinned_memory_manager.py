"""SimplePinnedMemoryManager 单元测试.

覆盖范围:
- 延迟初始化 memory_service
- 获取/更新 2 字段置顶记忆
- get_memory_for_analysis: 无编号格式化
- apply_operations: add/delete/change 精确字符串匹配
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.agent.memory.local_memory.pinned_memory import (
    SimplePinnedMemoryManager,
)
from src.core.types import MemoryOperation


class _StubEmbeddings:
    """可控向量桩: 按 text->vector 映射返回向量, 缺省零向量.

    用于验证 add 语义去重: 让语义重复对映射到同一向量(余弦=1.0>=阈值),
    非重复对映射到正交向量(余弦=0.0<阈值). 记录调用以断言短路行为.
    """

    def __init__(self, vector_map: dict[str, list[float]]) -> None:
        self.vector_map = vector_map
        self.query_calls: list[str] = []
        self.doc_calls: list[list[str]] = []

    async def aembed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return self.vector_map.get(text, [0.0, 0.0])

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.doc_calls.append(list(texts))
        return [self.vector_map.get(t, [0.0, 0.0]) for t in texts]


class _ErrorEmbeddings:
    """恒抛异常的桩, 验证 embedding 失败时回退精确匹配."""

    async def aembed_query(self, text: str) -> list[float]:
        raise RuntimeError("boom")

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("boom")


class TestSimplePinnedMemoryManager:
    @pytest.fixture
    def manager(self, test_user: str, test_thread_id: str) -> SimplePinnedMemoryManager:
        return SimplePinnedMemoryManager(
            test_user, test_thread_id, agent_id="test-agent"
        )

    @pytest.mark.asyncio
    async def test_get_memory_for_audit_should_return_numbered_block_and_map(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """测试审计格式化: 应返回带[N]编号块(跨字段连续) + number_map."""
        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "姓名：张三\n城市：北京",
                "preferences": "偏好暗色主题",
            }
            mock_create.return_value = mock_service

            block, number_map = await manager.get_memory_for_audit()

        assert "[1] 姓名：张三" in block
        assert "[2] 城市：北京" in block
        assert "[3] 偏好暗色主题" in block
        assert "### 基本画像" in block
        assert "### 口味偏好" in block
        assert number_map[1] == {"field": "basic_info", "content": "姓名：张三"}
        assert number_map[2] == {"field": "basic_info", "content": "城市：北京"}
        assert number_map[3] == {"field": "preferences", "content": "偏好暗色主题"}

    @pytest.mark.asyncio
    async def test_get_memory_for_audit_empty_memory_returns_empty_map(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """空记忆时 number_map 为空."""
        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "",
                "preferences": "",
            }
            mock_create.return_value = mock_service

            _block, number_map = await manager.get_memory_for_audit()

        assert number_map == {}

    @pytest.mark.asyncio
    async def test_get_memory_service_lazy_initialization_should_create_single_instance(
        self,
        manager: SimplePinnedMemoryManager,
        test_user: str,
        test_thread_id: str,
    ) -> None:
        """测试懒加载: 应创建单一服务实例"""
        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_create.return_value = AsyncMock()

            service1 = await manager._get_memory_service()
            service2 = await manager._get_memory_service()
            assert service1 is service2
            mock_create.assert_called_once_with(
                test_user, test_thread_id, agent_id="test-agent"
            )

    @pytest.mark.asyncio
    async def test_get_pinned_memory_content_should_return_dict_when_success(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """测试获取置顶记忆: 成功时应返回2字段字典"""
        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "A",
                "preferences": "B",
            }
            mock_create.return_value = mock_service

            result = await manager.get_pinned_memory_content()
            assert result == {"basic_info": "A", "preferences": "B"}

    @pytest.mark.asyncio
    async def test_get_pinned_memory_content_should_return_empty_dict_when_error(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """测试获取置顶记忆: 出错时应返回空字典"""
        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_dm = AsyncMock()
            mock_dm.get_simple_memories_as_dict.side_effect = Exception("boom")
            mock_create.return_value = mock_dm

            result = await manager.get_pinned_memory_content()
            assert result == {"basic_info": "", "preferences": ""}

    @pytest.mark.asyncio
    async def test_get_memory_for_analysis_should_format_without_ids(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """测试get_memory_for_analysis: 应返回纯文本, 无编号"""
        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "地址: 南京\n邮箱: test@test.com",
                "preferences": "喜欢猫",
            }
            mock_create.return_value = mock_service

            block = await manager.get_memory_for_analysis()

            assert isinstance(block, str)
            assert "地址: 南京" in block
            assert "邮箱: test@test.com" in block
            assert "喜欢猫" in block
            # 无编号前缀
            assert "[1]" not in block
            assert "[2]" not in block

    @pytest.mark.asyncio
    async def test_get_memory_for_analysis_empty_memory(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """测试get_memory_for_analysis: 空记忆应含(空)标记"""
        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "",
                "preferences": "",
            }
            mock_create.return_value = mock_service

            block = await manager.get_memory_for_analysis()

            assert isinstance(block, str)
            assert "(空)" in block

    @pytest.mark.asyncio
    async def test_apply_operations_add_should_append_and_clear_cache(
        self,
        manager: SimplePinnedMemoryManager,
        test_user: str,
        test_thread_id: str,
    ) -> None:
        """测试apply_operations: add操作应追加条目并清理置顶缓存.

        主历史缓存与置顶独立, 故不再清 conversation 缓存.
        """
        with (
            patch(
                "src.agent.memory.local_memory.pinned_memory.create_memory_service"
            ) as mock_create,
            patch(
                "src.agent.memory.local_memory.cache.clear_pinned_memory"
            ) as mock_clear_pinned,
        ):
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "已有条目",
                "preferences": "",
            }
            mock_service.update_memory.return_value = AsyncMock()
            mock_create.return_value = mock_service

            operations = [
                MemoryOperation(action="add", field="basic_info", content="新条目"),
            ]

            result = await manager.apply_operations(operations)
            assert result is True
            assert mock_service.update_memory.call_count == 1
            mock_clear_pinned.assert_called_once_with(
                test_user, test_thread_id, agent_id="test-agent"
            )

    @pytest.mark.asyncio
    async def test_apply_operations_add_should_skip_duplicate(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """测试apply_operations: add重复条目应跳过"""
        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "已有条目",
                "preferences": "",
            }
            mock_service.update_memory.return_value = AsyncMock()
            mock_create.return_value = mock_service

            operations = [
                MemoryOperation(action="add", field="basic_info", content="已有条目"),
            ]

            result = await manager.apply_operations(operations)
            assert result is False
            mock_service.update_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_operations_delete_should_remove_on_exact_match(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """测试apply_operations: delete精确匹配应移除"""
        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "条目A\n条目B",
                "preferences": "",
            }
            mock_service.update_memory.return_value = AsyncMock()
            mock_create.return_value = mock_service

            operations = [
                MemoryOperation(action="delete", field="basic_info", content="条目A"),
            ]

            result = await manager.apply_operations(operations)
            assert result is True

            call_args = mock_service.update_memory.call_args
            content_arg = str(call_args)
            assert "条目B" in content_arg
            assert "条目A" not in content_arg

    @pytest.mark.asyncio
    async def test_apply_operations_delete_no_match_should_skip(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """测试apply_operations: delete未命中精确匹配应跳过"""
        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "条目A",
                "preferences": "",
            }
            mock_create.return_value = mock_service

            operations = [
                MemoryOperation(action="delete", field="basic_info", content="不存在"),
            ]

            result = await manager.apply_operations(operations)
            assert result is False
            mock_service.update_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_operations_change_should_replace_on_exact_match(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """测试apply_operations: change精确匹配old_content应替换为new_content"""
        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "旧内容",
                "preferences": "",
            }
            mock_service.update_memory.return_value = AsyncMock()
            mock_create.return_value = mock_service

            operations = [
                MemoryOperation(
                    action="change",
                    field="basic_info",
                    old_content="旧内容",
                    new_content="新内容",
                ),
            ]

            result = await manager.apply_operations(operations)
            assert result is True

            call_args = mock_service.update_memory.call_args
            assert "新内容" in str(call_args)
            assert "旧内容" not in str(call_args)

    @pytest.mark.asyncio
    async def test_apply_operations_change_no_match_should_skip(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """测试apply_operations: change未命中old_content应跳过"""
        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "内容",
                "preferences": "",
            }
            mock_create.return_value = mock_service

            operations = [
                MemoryOperation(
                    action="change",
                    field="basic_info",
                    old_content="不存在",
                    new_content="新内容",
                ),
            ]

            result = await manager.apply_operations(operations)
            assert result is False
            mock_service.update_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_operations_empty_list_should_return_false(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """测试apply_operations: 空操作列表应返回False"""
        result = await manager.apply_operations([])
        assert result is False

    @pytest.mark.asyncio
    async def test_apply_operations_invalid_field_should_skip(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """测试apply_operations: 无效field应跳过不报错"""
        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "内容",
                "preferences": "",
            }
            mock_create.return_value = mock_service

            operations = [
                MemoryOperation(action="add", field="unknown", content="新条目"),
            ]

            result = await manager.apply_operations(operations)
            assert result is False
            mock_service.update_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_audit_parse_operations_then_apply_end_to_end(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """回归: 审计 parse_operations 输出必须能被 apply_operations 正确消费.

        修复前 parse_operations 返回裸 dict, apply_operations 用 op.field 属性访问
        会崩 'dict' object has no attribute 'field' (实测 R20/R21/R41 全失败).
        """
        from src.inference.content_analyzer.pinned_memory_audit_analyzer import (
            parse_operations,
        )

        number_map = {
            1: {"field": "basic_info", "content": "噪音条目"},
            2: {"field": "preferences", "content": "旧偏好"},
        }
        audit_content = (
            '{"operations":['
            '{"action":"delete","number":1,"reason":"过时"},'
            '{"action":"change","number":2,"new_content":"新偏好","reason":"提炼"}'
            "]}"
        )
        # 审计输出 (现在是 MemoryOperation 对象)
        operations = parse_operations(audit_content, number_map)
        assert all(isinstance(op, MemoryOperation) for op in operations)

        with patch(
            "src.agent.memory.local_memory.pinned_memory.create_memory_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "噪音条目\n保留条目",
                "preferences": "旧偏好",
            }
            mock_service.update_memory.return_value = AsyncMock()
            mock_create.return_value = mock_service

            # 修复前: 下一行抛 'dict' object has no attribute 'field'
            result = await manager.apply_operations(operations)

        assert result is True
        # 两个字段都被修改 (modified_fields 是 set, 顺序不定, 按内容匹配)
        calls = [str(c) for c in mock_service.update_memory.call_args_list]
        basic_call = next(c for c in calls if "保留条目" in c or "噪音条目" in c)
        assert "保留条目" in basic_call
        assert "噪音条目" not in basic_call
        pref_call = next(c for c in calls if "新偏好" in c or "旧偏好" in c)
        assert "新偏好" in pref_call
        assert "旧偏好" not in pref_call

    @pytest.mark.asyncio
    async def test_apply_operations_add_skip_semantic_duplicate(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """add 与已有条目语义重复(相似度>=阈值)应跳过, 不写库."""
        stub = _StubEmbeddings({
            "偏好暗色主题": [1.0, 0.0],
            "喜欢深色界面": [1.0, 0.0],
        })
        with (
            patch(
                "src.agent.memory.local_memory.pinned_memory.create_memory_service"
            ) as mock_create,
            patch(
                "src.inference.embeddings.embeddings.create_embeddings",
                return_value=stub,
            ),
        ):
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "偏好暗色主题",
                "preferences": "",
            }
            mock_service.update_memory.return_value = AsyncMock()
            mock_create.return_value = mock_service

            operations = [
                MemoryOperation(
                    action="add", field="basic_info", content="喜欢深色界面"
                ),
            ]
            result = await manager.apply_operations(operations)

        assert result is False
        mock_service.update_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_operations_add_kept_when_semantic_below_threshold(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """add 与已有条目语义不重复(相似度<阈值)应保留."""
        stub = _StubEmbeddings({
            "偏好暗色主题": [1.0, 0.0],
            "住在杭州": [0.0, 1.0],
        })
        with (
            patch(
                "src.agent.memory.local_memory.pinned_memory.create_memory_service"
            ) as mock_create,
            patch(
                "src.inference.embeddings.embeddings.create_embeddings",
                return_value=stub,
            ),
        ):
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "偏好暗色主题",
                "preferences": "",
            }
            mock_service.update_memory.return_value = AsyncMock()
            mock_create.return_value = mock_service

            operations = [
                MemoryOperation(action="add", field="basic_info", content="住在杭州"),
            ]
            result = await manager.apply_operations(operations)

        assert result is True
        assert mock_service.update_memory.call_count == 1

    @pytest.mark.asyncio
    async def test_apply_operations_add_semantic_dedup_disabled_keeps(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """dedup 关闭时即使语义重复也保留, 且不触发 embedding 调用."""
        manager._dedup_enabled = False
        stub = _StubEmbeddings({
            "偏好暗色主题": [1.0, 0.0],
            "喜欢深色界面": [1.0, 0.0],
        })
        with (
            patch(
                "src.agent.memory.local_memory.pinned_memory.create_memory_service"
            ) as mock_create,
            patch(
                "src.inference.embeddings.embeddings.create_embeddings",
                return_value=stub,
            ),
        ):
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "偏好暗色主题",
                "preferences": "",
            }
            mock_service.update_memory.return_value = AsyncMock()
            mock_create.return_value = mock_service

            operations = [
                MemoryOperation(
                    action="add", field="basic_info", content="喜欢深色界面"
                ),
            ]
            result = await manager.apply_operations(operations)

        assert result is True
        assert mock_service.update_memory.call_count == 1
        assert stub.query_calls == []
        assert stub.doc_calls == []

    @pytest.mark.asyncio
    async def test_apply_operations_add_fallback_on_embedding_error(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """embedding 调用抛异常应回退为正常 add(不阻断主流程)."""
        with (
            patch(
                "src.agent.memory.local_memory.pinned_memory.create_memory_service"
            ) as mock_create,
            patch(
                "src.inference.embeddings.embeddings.create_embeddings",
                return_value=_ErrorEmbeddings(),
            ),
        ):
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "偏好暗色主题",
                "preferences": "",
            }
            mock_service.update_memory.return_value = AsyncMock()
            mock_create.return_value = mock_service

            operations = [
                MemoryOperation(action="add", field="basic_info", content="住在杭州"),
            ]
            result = await manager.apply_operations(operations)

        assert result is True
        assert mock_service.update_memory.call_count == 1

    @pytest.mark.asyncio
    async def test_apply_operations_add_exact_dup_short_circuits_embedding(
        self, manager: SimplePinnedMemoryManager
    ) -> None:
        """精确重复应优先短路, 不触发 embedding 调用."""
        stub = _StubEmbeddings({})
        with (
            patch(
                "src.agent.memory.local_memory.pinned_memory.create_memory_service"
            ) as mock_create,
            patch(
                "src.inference.embeddings.embeddings.create_embeddings",
                return_value=stub,
            ),
        ):
            mock_service = AsyncMock()
            mock_service.get_pinned_memory_as_dict.return_value = {
                "basic_info": "已有条目",
                "preferences": "",
            }
            mock_service.update_memory.return_value = AsyncMock()
            mock_create.return_value = mock_service

            operations = [
                MemoryOperation(action="add", field="basic_info", content="已有条目"),
            ]
            result = await manager.apply_operations(operations)

        assert result is False
        mock_service.update_memory.assert_not_called()
        assert stub.query_calls == []
        assert stub.doc_calls == []
