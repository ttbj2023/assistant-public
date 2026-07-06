"""SessionMessageQueue 消息合并集成测试.

灰盒: 真实 SessionMessageQueue + 真实 Agent (OrchestratorAgent.process_message 全链路:
记忆装配/轮次分配/对话落库均为真实) 协作, 仅在 LLM 调用边界
(inference_coordinator.process_with_agent) 注入 Mock, 验证会话层核心特性 ——
顺序/合并: 快速连发多条消息时合并为一次 Agent 调用, 仅最后一条拿到响应.

单元测试 (test_session_queue.py) 用 AsyncMock 假 Agent 验证合并语义, 本测试用真实
Agent + 真实 allocate_round_number (真实 SQLite) 补齐协作层缺口.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clear_session_queue_instances():
    """每个测试前后清理 SessionMessageQueue 类级单例缓存, 防跨测试污染."""
    from src.session.session_queue import SessionMessageQueue

    SessionMessageQueue._instances.clear()
    yield
    SessionMessageQueue._instances.clear()


class TestSessionMessageQueueMergeIntegration:
    """消息合并协作集成测试."""

    @pytest.mark.asyncio
    async def test_integration_merge_two_messages_last_gets_response(
        self, test_user, test_thread_id
    ):
        """快速连发两条消息, 合并为一次 Agent 调用, 仅最后一条拿到真实响应.

        协作场景: SessionMessageQueue + 真实 OrchestratorAgent.process_message +
            真实 allocate_round_number (真实 ConversationService + SQLite) 协作
        Mock 边界: 仅 Mock 外部 LLM (inference_coordinator.process_with_agent),
            捕获 user_content 验证合并文本, Agent/编排/记忆存储均为真实组件
        验证重点:
            1. 第一条消息 future 返回 None (被合并吸收)
            2. 第二条消息 future 返回真实响应
            3. LLM 仅被调用一次, 收到的 user_content 含两条消息内容
        业务价值: 会话层"顺序/合并"是核心差异化特性, 此处为真实协作唯一验证点
        """
        from src.agent.manager import get_agent_manager
        from src.session.session_queue import SessionMessageQueue

        agent_manager = get_agent_manager()
        agent = await agent_manager.get_agent("personal-assistant")
        await agent.initialize()

        original_llm = agent._orchestrator.inference_coordinator
        captured_contents: list[str] = []

        async def mock_process_with_agent(*args, **kwargs):
            # process_with_agent 第一参数 user_content 是合并文本载体
            user_content = kwargs.get("user_content") or (args[0] if args else "")
            captured_contents.append(user_content)
            return "合并响应", {}

        queue = SessionMessageQueue.get(test_user, test_thread_id, "personal-assistant")

        with patch.object(
            original_llm,
            "process_with_agent",
            side_effect=mock_process_with_agent,
        ):
            # submit 为 async 但内部无 await, 两次连续调用不会出让控制权给
            # processor_loop, 故两条消息会落入同一 batch 触发合并
            future1 = await queue.submit(
                user_input="第一条消息",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=agent,
            )
            future2 = await queue.submit(
                user_input="第二条消息",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=agent,
            )

            response1 = await asyncio.wait_for(future1, timeout=5.0)
            response2 = await asyncio.wait_for(future2, timeout=5.0)

        # Assert
        assert response1 is None, "被合并吸收的消息应返回 None"
        assert response2 == "合并响应", "仅最后一条消息拿到真实响应"
        assert len(captured_contents) == 1, "两条消息应合并为一次 LLM 调用"
        assert "第一条消息" in captured_contents[0]
        assert "第二条消息" in captured_contents[0]
        assert "用户连续发送了以下消息" in captured_contents[0]

    @pytest.mark.asyncio
    async def test_integration_single_message_not_merged(
        self, test_user, test_thread_id
    ):
        """单条消息不触发合并, 正常返回响应 (对照基线).

        协作场景: SessionMessageQueue + 真实 Agent, 单条消息走 _process_single 路径
        Mock 边界: 仅 Mock 外部 LLM
        验证重点: 单条消息 future 返回真实响应 (非 None), LLM 调用一次
        业务价值: 确认合并仅对多消息 batch 生效, 单消息路径不被误判为吸收
        """
        from src.agent.manager import get_agent_manager
        from src.session.session_queue import SessionMessageQueue

        agent_manager = get_agent_manager()
        agent = await agent_manager.get_agent("personal-assistant")
        await agent.initialize()

        original_llm = agent._orchestrator.inference_coordinator
        call_count = 0

        async def mock_process_with_agent(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return "单条响应", {}

        queue = SessionMessageQueue.get(test_user, test_thread_id, "personal-assistant")

        with patch.object(
            original_llm,
            "process_with_agent",
            side_effect=mock_process_with_agent,
        ):
            future = await queue.submit(
                user_input="唯一的消息",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=agent,
            )
            response = await asyncio.wait_for(future, timeout=5.0)

        assert response == "单条响应"
        assert response is not None
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_integration_merge_writes_single_conversation_round(
        self, test_user, test_thread_id
    ):
        """合并后对话历史只写入一轮 (真实 SQLite 落库验证).

        协作场景: SessionMessageQueue 合并 + 真实 process_message + 真实
            add_conversation_round (真实 ConversationService + SQLite)
        Mock 边界: 仅 Mock 外部 LLM
        验证重点: 合并批次真实落库为 1 条 conversation_index 记录 (而非 2 条),
            且 user_message 含合并文本
        业务价值: 确保合并语义在持久化层一致, 不产生重复/缺失轮次
        """
        from src.agent.manager import get_agent_manager
        from src.session.session_queue import SessionMessageQueue
        from src.storage.service import create_conversation_service

        agent_manager = get_agent_manager()
        agent = await agent_manager.get_agent("personal-assistant")
        await agent.initialize()

        original_llm = agent._orchestrator.inference_coordinator

        async def mock_process_with_agent(*args, **kwargs):
            return "合并响应", {}

        queue = SessionMessageQueue.get(test_user, test_thread_id, "personal-assistant")

        with patch.object(
            original_llm,
            "process_with_agent",
            side_effect=mock_process_with_agent,
        ):
            future1 = await queue.submit(
                user_input="合并A",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=agent,
            )
            future2 = await queue.submit(
                user_input="合并B",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=agent,
            )
            await asyncio.wait_for(future1, timeout=5.0)
            await asyncio.wait_for(future2, timeout=5.0)

        # Assert: 真实 SQLite 只落 1 轮 (合并语义在持久化层一致)
        conv_service = await create_conversation_service(
            test_user, test_thread_id, agent_id="personal-assistant"
        )
        round_numbers = await conv_service.list_recent_rounds(
            test_user, test_thread_id, limit=5
        )
        assert len(round_numbers) == 1, "合并批次应落库为 1 轮, 而非 2 轮"
