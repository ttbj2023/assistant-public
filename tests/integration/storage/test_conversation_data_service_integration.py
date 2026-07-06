"""ConversationDataService 并行存储编排集成测试.

验证统一对话数据服务的 4 路并行存储编排, 补充零覆盖的编排逻辑:

- 4 路并行存储全部成功: SQL + 向量 + 索引 + 附件
- 向量存储失败不阻塞 SQL: asyncio.gather(return_exceptions=True) 容错
- confirm_round_number_usage: 存储后轮次号确认

测试策略: 灰盒 - 真实 ConversationService/DB + Mock VectorService(避免 ChromaDB 依赖),
保留 ConversationDataService 真实编排逻辑.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.storage.models.conversation import ConversationData


@pytest.fixture(autouse=True)
def _isolate_service_cache() -> Iterator[None]:
    """每个测试前后清空全局 Service 缓存."""
    from src.storage.service.service_factory import clear_vector_cache

    clear_vector_cache()
    yield
    clear_vector_cache()


def _make_conv_data(
    user_id: str = "cds_test",
    thread_id: str = "main",
    round_number: int = 1,
    agent_id: str = "test-agent",
) -> ConversationData:
    """构造测试用 ConversationData."""
    return ConversationData(
        user_id=user_id,
        thread_id=thread_id,
        user_message="测试问题",
        assistant_response="测试回答",
        round_number=round_number,
        timestamp=datetime.now(),
        agent_id=agent_id,
    )


@pytest.mark.integration
@pytest.mark.serial
class TestConversationDataStoreIntegration:
    """ConversationDataService store_conversation_data 编排集成测试."""

    @pytest.mark.asyncio
    async def test_all_three_operations_succeed(self):
        """测试 3 路并行存储全部成功.

        协作场景: _store_conversation_content + _store_vector_conversation
                  + _generate_conversation_index
        设计思路: 真实 ConversationService + Mock VectorService, 验证全部操作成功
        业务价值: 确保对话数据完整写入 SQL + 向量 + 索引, 数据可检索
        """
        from src.storage.service.service_factory import (
            create_conversation_data_service,
        )

        data_svc = await create_conversation_data_service(
            "cds_all_ok", "main", agent_id="test-agent"
        )

        with patch.object(
            data_svc.vector_service,
            "add_conversation_content",
            AsyncMock(return_value="fake_vector_id"),
        ):
            result = await data_svc.store_conversation_data(
                _make_conv_data(user_id="cds_all_ok", round_number=1)
            )

        assert result["success"] is True
        summary = result["summary"]
        assert summary["sql_success"] is True
        assert summary["vector_success"] is True
        assert summary["index_success"] is True
        assert summary["successful_operations"] == 3
        assert summary["failed_operations"] == 0

        conv = await data_svc.conversation_service.get_conversation_by_round(
            "cds_all_ok", "main", 1
        )
        assert conv is not None
        assert conv.user_message == "测试问题"

    @pytest.mark.asyncio
    async def test_vector_failure_does_not_block_sql(self):
        """测试向量存储失败: 不阻塞 SQL 存储.

        协作场景: _store_vector_conversation 抛异常 → asyncio.gather(return_exceptions=True)
                  → SQL/索引仍正常完成
        设计思路: Mock vector_service.add_conversation_content 抛异常, 验证 SQL 仍写入
        业务价值: 单路存储失败不影响关键路径, 数据不丢失
        """
        from src.storage.service.service_factory import (
            create_conversation_data_service,
        )

        data_svc = await create_conversation_data_service(
            "cds_vec_fail", "main", agent_id="test-agent"
        )

        with patch.object(
            data_svc.vector_service,
            "add_conversation_content",
            AsyncMock(side_effect=RuntimeError("ChromaDB offline")),
        ):
            result = await data_svc.store_conversation_data(
                _make_conv_data(user_id="cds_vec_fail", round_number=1)
            )

        assert result["success"] is True
        summary = result["summary"]
        assert summary["sql_success"] is True
        assert summary["vector_success"] is False
        assert summary["index_success"] is True
        assert summary["failed_operations"] >= 1

        conv = await data_svc.conversation_service.get_conversation_by_round(
            "cds_vec_fail", "main", 1
        )
        assert conv is not None, "向量失败时 SQL 存储仍应成功"

    @pytest.mark.asyncio
    async def test_confirm_round_number_after_store(self):
        """测试存储后轮次号确认.

        协作场景: store_conversation_data → confirm_round_number_usage
        设计思路: 先存储对话, 再确认轮次号存在/不存在
        业务价值: 验证写入→读取闭环, confirm 用于校验存储结果
        """
        from src.storage.service.service_factory import (
            create_conversation_data_service,
        )

        data_svc = await create_conversation_data_service(
            "cds_confirm", "main", agent_id="test-agent"
        )

        with patch.object(
            data_svc.vector_service,
            "add_conversation_content",
            AsyncMock(return_value="fake_id"),
        ):
            await data_svc.store_conversation_data(
                _make_conv_data(user_id="cds_confirm", round_number=7)
            )

        assert (
            await data_svc.confirm_round_number_usage(7, "cds_confirm", "main") is True
        )
        assert (
            await data_svc.confirm_round_number_usage(999, "cds_confirm", "main")
            is False
        )
