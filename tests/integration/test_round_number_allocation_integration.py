"""ConversationService 轮次号分配集成测试.

## 测试策略文档

### Mock边界定义
无 Mock - 使用真实的 ConversationService + 真实 SQLite 数据库.
Service 通过 create_conversation_service 工厂创建, 内部使用 path_resolver 管理路径,
测试环境自动指向 ./test_data/{user_id}/{thread_id}/.

### 协作场景覆盖
1. ConversationService + AsyncConversationIndexDAO + SQLite → MAX+1 分配验证
2. allocate_round_number + create_conversation → 自动/预分配一致性验证
3. 用户-线程隔离 → 多租户数据隔离验证

### 业务价值
确保对话历史轮次号分配的正确性和连续性, 这是双路检索记忆系统排序的基础.
ConversationService.allocate_round_number 的 MAX+1 事务逻辑此前无任何测试覆盖.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestConversationServiceRoundNumberIntegration:
    """测试 ConversationService 轮次号分配的真实数据库协作."""

    @pytest.mark.asyncio
    async def test_allocate_returns_one_for_empty_database(
        self, test_user: str, test_thread_id: str
    ):
        """测试空库首次分配轮次号应返回1.

        协作场景: ConversationService + AsyncConversationIndexDAO + SQLite → MAX+1 分配
        设计思路: 真实数据库无记录时, MAX(round_number)=0, 新轮次号应为1
        Mock边界: 无 Mock, 使用真实 Service + 真实 SQLite
        业务价值: 验证轮次号分配的起始正确性
        """
        from src.storage.service.service_factory import create_conversation_service

        service = await create_conversation_service(
            test_user, test_thread_id, agent_id="test-agent"
        )

        result = await service.allocate_round_number(test_user, test_thread_id)

        assert result == 1

    @pytest.mark.asyncio
    async def test_allocate_increments_after_conversations_created(
        self, test_user: str, test_thread_id: str
    ):
        """测试已有对话记录后轮次号应递增.

        协作场景: create_conversation + allocate_round_number → 连续性验证
        设计思路: 写入N条对话后, allocate应返回N+1
        Mock边界: 无 Mock, 使用真实 Service + 真实 SQLite
        业务价值: 确保多轮对话的轮次号连续递增, 为记忆检索排序提供基础
        """
        from src.storage.service.service_factory import create_conversation_service

        service = await create_conversation_service(
            test_user, test_thread_id, agent_id="test-agent"
        )

        for i in range(1, 4):
            await service.create_conversation(
                user_message=f"用户消息{i}",
                assistant_response=f"助手回复{i}",
                user_id=test_user,
                thread_id=test_thread_id,
                agent_id="test-agent",
            )

        result = await service.allocate_round_number(test_user, test_thread_id)

        assert result == 4

    @pytest.mark.asyncio
    async def test_round_number_isolated_across_users_and_threads(
        self, test_user: str, test_thread_id: str
    ):
        """测试不同用户和线程的轮次号互不影响.

        协作场景: ConversationService + 路径隔离 → 多租户数据隔离
        设计思路: 两个不同线程各自独立分配, 互不干扰
        Mock边界: 无 Mock, 使用真实 Service + 真实 SQLite
        业务价值: 确保多用户多线程环境下轮次号不串扰
        """
        from src.storage.service.service_factory import create_conversation_service

        other_thread = f"{test_thread_id}_isolated"

        service_a = await create_conversation_service(
            test_user, test_thread_id, agent_id="test-agent"
        )
        service_b = await create_conversation_service(
            test_user, other_thread, agent_id="test-agent"
        )

        await service_a.create_conversation(
            user_message="消息A1",
            assistant_response="回复A1",
            user_id=test_user,
            thread_id=test_thread_id,
            agent_id="test-agent",
        )
        await service_a.create_conversation(
            user_message="消息A2",
            assistant_response="回复A2",
            user_id=test_user,
            thread_id=test_thread_id,
            agent_id="test-agent",
        )

        result_b = await service_b.allocate_round_number(test_user, other_thread)
        assert result_b == 1

        result_a = await service_a.allocate_round_number(test_user, test_thread_id)
        assert result_a == 3

    @pytest.mark.asyncio
    async def test_create_conversation_auto_assigns_sequential_round_numbers(
        self, test_user: str, test_thread_id: str
    ):
        """测试 create_conversation 不传 round_number 时自动顺序分配.

        协作场景: create_conversation(round_number=None) + allocate_round_number → 自动分配一致性
        设计思路: round_number=None 时内部调用 allocate, 应返回连续递增的轮次号
        Mock边界: 无 Mock, 使用真实 Service + 真实 SQLite
        业务价值: 确保自动分配与手动 allocate 逻辑一致, ProcessorOrchestrator 依赖此行为
        """
        from src.storage.service.service_factory import create_conversation_service

        service = await create_conversation_service(
            test_user, test_thread_id, agent_id="test-agent"
        )

        conv1 = await service.create_conversation(
            user_message="第一条",
            assistant_response="回复1",
            user_id=test_user,
            thread_id=test_thread_id,
            agent_id="test-agent",
        )
        assert conv1.round_number == 1

        conv2 = await service.create_conversation(
            user_message="第二条",
            assistant_response="回复2",
            user_id=test_user,
            thread_id=test_thread_id,
            agent_id="test-agent",
        )
        assert conv2.round_number == 2

        remaining = await service.allocate_round_number(test_user, test_thread_id)
        assert remaining == 3
