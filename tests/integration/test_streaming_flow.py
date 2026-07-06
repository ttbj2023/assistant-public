"""流式响应集成测试.

灰盒: 真实 AgentManager + AgentFactory + Agent 流式编排协作, 仅在 LLM 调用边界
(inference_coordinator.process_with_agent_stream) 注入 Mock 输出, 验证真实装配的
Agent 能正确传递流式响应. 该编排方法 (base_orchestrator_agent.process_message_stream)
无单测覆盖, 此处为唯一验证点.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestStreamingFlowIntegration:
    """流式处理集成测试."""

    @pytest.mark.asyncio
    async def test_integration_streaming_flow_forwards_llm_chunks(
        self, test_user, test_thread_id
    ):
        """真实 Agent 流式方法正确转发 LLM 输出分块.

        协作场景: AgentManager + AgentFactory + Agent.process_message_stream 真实协作
        Mock 边界: 仅 Mock 外部 LLM 流式输出 (inference_coordinator 调用边界),
            Agent/Manager/编排逻辑均为真实组件
        验证重点: process_message_stream 正确转发底层 stream 的每个分块, 顺序与数量一致
        业务价值: 确保真实装配的 Agent 能完整传递流式响应给前端
        """
        from src.agent.manager import get_agent_manager

        agent_manager = get_agent_manager()
        agent = await agent_manager.get_agent("personal-assistant")
        await agent.initialize()

        original_llm = agent._orchestrator.inference_coordinator

        async def mock_llm_stream(*args, **kwargs):
            yield "Hello"
            yield " World"

        with patch.object(
            original_llm,
            "process_with_agent_stream",
            side_effect=mock_llm_stream,
        ):
            chunks = []
            async for chunk in agent.process_message_stream(
                message="你好",
                user_id=test_user,
                thread_id=test_thread_id,
            ):
                chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks == ["Hello", " World"]

    @pytest.mark.asyncio
    async def test_integration_streaming_with_tool_invocation_content(
        self, test_user, test_thread_id
    ):
        """流式响应中包含工具调用相关内容时正确传递.

        协作场景: AgentManager + Agent + 流式编排, LLM 输出含工具调用语义内容
        Mock 边界: 仅 Mock 外部 LLM 流式输出
        验证重点: 多分块内容 (含工具调用语义) 完整传递, 首尾分块内容正确
        业务价值: 确保工具调用场景下的流式响应不被截断或丢失
        """
        from src.agent.manager import get_agent_manager

        agent_manager = get_agent_manager()
        agent = await agent_manager.get_agent("personal-assistant")
        await agent.initialize()

        original_llm = agent._orchestrator.inference_coordinator

        async def mock_llm_with_tools(*args, **kwargs):
            yield "让我帮你查询TODO列表"
            yield "这是TODO列表"

        with patch.object(
            original_llm,
            "process_with_agent_stream",
            side_effect=mock_llm_with_tools,
        ):
            chunks = []
            async for chunk in agent.process_message_stream(
                message="查看TODO",
                user_id=test_user,
                thread_id=test_thread_id,
            ):
                if chunk:
                    chunks.append(chunk)

        assert len(chunks) >= 2
        assert "让我帮你查询TODO列表" in chunks[0]
        assert chunks[-1] == "这是TODO列表"

    @pytest.mark.asyncio
    async def test_integration_streaming_propagates_llm_error(
        self, test_user, test_thread_id
    ):
        """流式处理中 LLM 中途出错时错误正确传播.

        协作场景: AgentManager + Agent + 流式编排, LLM 流中途抛异常
        Mock 边界: 仅 Mock 外部 LLM (中途抛 RuntimeError)
        验证重点: 错误从 stream 链路正确传播到调用方 (pytest.raises 捕获)
        业务价值: 确保流式错误不被静默吞没, 调用方可感知并处理
        """
        from src.agent.manager import get_agent_manager

        agent_manager = get_agent_manager()
        agent = await agent_manager.get_agent("personal-assistant")
        await agent.initialize()

        original_llm = agent._orchestrator.inference_coordinator

        async def mock_llm_error(*args, **kwargs):
            yield "开始"
            raise RuntimeError("LLM API错误")

        with (
            patch.object(
                original_llm,
                "process_with_agent_stream",
                side_effect=mock_llm_error,
            ),
            pytest.raises(RuntimeError),
        ):
            async for _ in agent.process_message_stream(
                message="测试",
                user_id=test_user,
                thread_id=test_thread_id,
            ):
                pass

    @pytest.mark.asyncio
    async def test_integration_streaming_handles_consecutive_requests(
        self, test_user, test_thread_id
    ):
        """同一 Agent 连续多次流式请求互不干扰.

        协作场景: 单 Agent 实例连续处理 3 次流式请求 (复用 AgentManager 缓存)
        Mock 边界: 仅 Mock 外部 LLM 流式输出
        验证重点: 连续请求各自独立完成, 不因前次请求残留状态而失败
        业务价值: 确保长生命周期 Agent 实例可安全复用处理多轮对话
        """
        from src.agent.manager import get_agent_manager

        agent_manager = get_agent_manager()
        agent = await agent_manager.get_agent("personal-assistant")
        await agent.initialize()

        original_llm = agent._orchestrator.inference_coordinator

        async def mock_llm_stream(*args, **kwargs):
            yield "OK"

        with patch.object(
            original_llm,
            "process_with_agent_stream",
            side_effect=mock_llm_stream,
        ):
            for i in range(3):
                chunks = []
                async for chunk in agent.process_message_stream(
                    message=f"请求{i + 1}",
                    user_id=test_user,
                    thread_id=test_thread_id,
                ):
                    chunks.append(chunk)

                assert chunks == ["OK"]

    @pytest.mark.asyncio
    async def test_integration_streaming_handles_long_response_chunks(
        self, test_user, test_thread_id
    ):
        """长响应 (大量分块) 流式处理完整无丢失.

        协作场景: AgentManager + Agent + 流式编排, LLM 输出 100 个分块
        Mock 边界: 仅 Mock 外部 LLM 流式输出 (yield 100 次)
        验证重点: 100 个分块全部按序传递, 首尾分块内容正确, 无丢失/乱序
        业务价值: 确保长文本生成场景下流式响应的完整性
        """
        from src.agent.manager import get_agent_manager

        agent_manager = get_agent_manager()
        agent = await agent_manager.get_agent("personal-assistant")
        await agent.initialize()

        original_llm = agent._orchestrator.inference_coordinator

        async def mock_llm_long(*args, **kwargs):
            for i in range(100):
                yield f"块{i}"

        with patch.object(
            original_llm,
            "process_with_agent_stream",
            side_effect=mock_llm_long,
        ):
            chunks = []
            async for chunk in agent.process_message_stream(
                message="生成长文本",
                user_id=test_user,
                thread_id=test_thread_id,
            ):
                chunks.append(chunk)

        assert len(chunks) == 100
        assert chunks[0] == "块0"
        assert chunks[-1] == "块99"


class TestStreamingErrorHandling:
    """流式错误处理集成测试."""

    @pytest.mark.asyncio
    async def test_integration_streaming_survives_finalize_failure(
        self, test_user, test_thread_id
    ):
        """对话落库 (finalize) 失败时不影响已产出的流式响应.

        协作场景: AgentManager + Agent + 流式编排 + finalize_conversation,
            finalize 抛异常时验证已流式产出的响应不受影响
        Mock 边界: Mock 外部 LLM 流式输出 + Mock finalize_conversation 抛异常
            (finalize 失败属内部错误传播场景, 验证降级行为)
        验证重点: finalize 失败不阻断已产出的流式分块, 响应内容完整
        业务价值: 确保记忆存储瞬时故障不导致用户收不到已生成的回复
        """
        from src.agent.manager import get_agent_manager

        agent_manager = get_agent_manager()
        agent = await agent_manager.get_agent("personal-assistant")
        await agent.initialize()

        original_llm = agent._orchestrator.inference_coordinator

        async def mock_llm_stream(*args, **kwargs):
            yield "响应内容"

        async def mock_finalize_error(*args, **kwargs):
            raise RuntimeError("记忆存储失败")

        with (
            patch.object(
                original_llm,
                "process_with_agent_stream",
                side_effect=mock_llm_stream,
            ),
            patch.object(
                agent,
                "finalize_conversation",
                side_effect=mock_finalize_error,
            ),
        ):
            chunks = []
            async for chunk in agent.process_message_stream(
                message="测试",
                user_id=test_user,
                thread_id=test_thread_id,
            ):
                chunks.append(chunk)

            assert chunks == ["响应内容"]
