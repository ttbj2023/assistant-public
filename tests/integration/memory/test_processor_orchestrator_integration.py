"""ProcessorOrchestrator 处理器总协调器集成测试.

验证记忆组装 + 推理 + 对话数据构建的完整协作, 补充单元测试过度 Mock 的部分:

- process 全链路: 真实记忆组装 → (Mock)推理 → 对话数据落库
- finalize 自动分配 round_number + 6 路并行存储
- finalize 的 exported_files 附件标记追加 (ContextVar 透传)
- 推理错误传播

测试策略: 灰盒 - 真实 ProcessorOrchestrator + LocalMemoryProcessor + MemoryAssembler +
ConversationMemoryCore + 全部 SQL Service + SQLite, 仅 Mock 真正的外部依赖
(推理协调器的 LLM 调用 + 索引/置顶 LLM 分析器 + 向量服务).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.memory.local_memory import pinned_memory_service
from src.agent.processors.processor_orchestrator import ProcessorOrchestrator
from src.config.agent_config import AgentConfig
from src.core.context import (
    UserContext,
    reset_user_context,
    set_user_context,
)
from src.storage.service.service_factory import (
    create_conversation_service,
)

_AGENT_ID = "test-agent"


async def _drain_pinned_bg_tasks() -> None:
    """等待所有置顶后台任务完成 (fire-and-forget)."""
    pending = list(pinned_memory_service.get_bg_tasks())
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _make_orchestrator() -> ProcessorOrchestrator:
    """构造真实 ProcessorOrchestrator (真实 LocalMemoryProcessor + InferenceCoordinator)."""
    return ProcessorOrchestrator(config=None, memory_type="local")


def _make_processor_config() -> dict:
    """构造 processor_config (含 system_prompt + agent_config)."""
    return {
        "system_prompt": "你是测试助手",
        "agent_config": AgentConfig(agent_id=_AGENT_ID),
    }


@pytest.mark.integration
class TestProcessorOrchestratorIntegration:
    """ProcessorOrchestrator process/finalize 集成测试."""

    @pytest.mark.asyncio
    async def test_integration_orchestrator_process_full_pipeline(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """测试 process 全链路: 真实记忆组装 → 推理 → 对话数据落库.

        协作场景: ProcessorOrchestrator.process 编排
                  LocalMemoryProcessor.build_messages_context (真实) +
                  InferenceCoordinator.process_with_agent (Mock) +
                  ConversationMemoryCore.add_conversation_round (6路真实存储)
        Mock 边界: process_with_agent (主 LLM) + llm_mocks (索引/置顶/向量)
        验证重点: 响应内容正确 / process_with_agent 收到非空历史 / 对话数据落库可读回
        业务价值: 端到端正向链路 (对应迁出的 test_process_with_conversation_memory_update)
        """
        conv_svc = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        await conv_svc.create_conversation(
            user_message="历史问题",
            assistant_response="历史回答",
            user_id=test_user,
            thread_id=test_thread_id,
            agent_id=_AGENT_ID,
            round_number=1,
        )

        orchestrator = _make_orchestrator()
        mock_inference = AsyncMock(
            return_value=("这是推理后的回复内容", {"processing_time": 0.5})
        )

        with patch.object(
            orchestrator.inference_coordinator, "process_with_agent", mock_inference
        ):
            response, stats, conv_data = await orchestrator.process(
                user_input="新问题",
                user_id=test_user,
                thread_id=test_thread_id,
                processor_config=_make_processor_config(),
                agent_id=_AGENT_ID,
            )

        await _drain_pinned_bg_tasks()

        assert response == "这是推理后的回复内容"

        call_kwargs = mock_inference.call_args
        history_arg = call_kwargs.kwargs.get("history_messages")
        assert history_arg, "process_with_agent 应收到非空历史消息 (记忆组装生效)"

        assert conv_data is not None
        stored = await conv_svc.get_conversation_by_round(test_user, test_thread_id, 2)
        assert stored is not None, "process 应落库 round 2 (历史为 round 1)"
        assert "这是推理后的回复内容" in stored.assistant_response

    @pytest.mark.asyncio
    async def test_integration_orchestrator_finalize_auto_allocates_round_number(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """测试 finalize 自动分配 round_number + 6 路并行存储.

        协作场景: finalize_conversation → get_or_create_conversation_memory (真实) →
                  _build_conversation_data (自动分配 round_number) →
                  add_conversation_round (6路真实存储)
        Mock 边界: llm_mocks (索引/置顶/向量), ContextVar 未设置 (返回 None)
        验证重点: round_number 自动从 1 分配 / 对话数据落库可读回 / 时间前缀格式
        业务价值: finalize 全流程 (对应迁出的 test_finalize_success)
        """
        orchestrator = _make_orchestrator()

        conv_data = await orchestrator.finalize_conversation(
            user_input="finalize 测试问题",
            response_content="finalize 测试回复",
            user_id=test_user,
            thread_id=test_thread_id,
            processor_config=_make_processor_config(),
            agent_id=_AGENT_ID,
        )
        await _drain_pinned_bg_tasks()

        assert conv_data is not None
        assert conv_data.round_number == 1, "空库应自动分配 round 1"

        conv_svc = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        stored = await conv_svc.get_conversation_by_round(test_user, test_thread_id, 1)
        assert stored is not None
        assert "finalize 测试回复" in stored.assistant_response
        assert conv_data.agent_id == _AGENT_ID

    @pytest.mark.asyncio
    async def test_integration_orchestrator_finalize_appends_attachment_markers_with_url(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """测试 finalize 在响应含 URL 时追加 exported_files 附件标记 (ContextVar 透传).

        协作场景: set_user_context(exported_files) → finalize →
                  _append_exported_file_markers 追加缺失的 [file: file_id] brief
        Mock 边界: llm_mocks, ContextVar 预设 exported_files
        验证重点: 落库的 assistant_response 含附件标记, 且保留原始响应内容
        业务价值: 对话历史存储的内部路径标记 (对应迁出的 test_finalize_with_attachments)
        """
        file_url = "https://files.example.com/abc/report.pdf"
        token = set_user_context(
            UserContext(
                user_id=test_user,
                thread_id=test_thread_id,
                agent_id=_AGENT_ID,
                exported_files=[
                    {
                        "url": file_url,
                        "file_id": "abc12345",
                        "brief": "季度报告",
                        "filename": "report.pdf",
                    }
                ],
            )
        )

        try:
            orchestrator = _make_orchestrator()
            conv_data = await orchestrator.finalize_conversation(
                user_input="生成报告",
                response_content=f"已生成报告: {file_url}",
                user_id=test_user,
                thread_id=test_thread_id,
                processor_config=_make_processor_config(),
                agent_id=_AGENT_ID,
            )
            await _drain_pinned_bg_tasks()
        finally:
            reset_user_context(token)

        assert conv_data is not None
        assert "[file: abc12345] 季度报告" in conv_data.assistant_response
        assert "已生成报告:" in conv_data.assistant_response

    @pytest.mark.asyncio
    async def test_integration_orchestrator_finalize_appends_markers_without_url(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """测试 LLM 原始输出不含 URL 时, finalize 仍自动追加附件标记.

        协作场景: set_user_context(exported_files) → finalize →
                  _append_exported_file_markers 在 assistant_response 末尾追加
                  [file: file_id] brief
        Mock 边界: llm_mocks, ContextVar 预设 exported_files
        验证重点: 落库 assistant_response 含附件标记, 且原始响应中无真实 URL
        业务价值: 修复后续轮次 LLM 看不到附件标记的问题
        """
        token = set_user_context(
            UserContext(
                user_id=test_user,
                thread_id=test_thread_id,
                agent_id=_AGENT_ID,
                exported_files=[
                    {
                        "url": "https://files.example.com/abc/report.pdf",
                        "file_id": "358dc44d",
                        "brief": "季度报告",
                        "filename": "report.pdf",
                    }
                ],
            )
        )

        try:
            orchestrator = _make_orchestrator()
            conv_data = await orchestrator.finalize_conversation(
                user_input="生成报告",
                response_content="已导出 👇",
                user_id=test_user,
                thread_id=test_thread_id,
                processor_config=_make_processor_config(),
                agent_id=_AGENT_ID,
            )
            await _drain_pinned_bg_tasks()
        finally:
            reset_user_context(token)

        assert conv_data is not None
        assert "[file: 358dc44d] 季度报告" in conv_data.assistant_response
        assert "https://files.example.com" not in conv_data.assistant_response
        assert conv_data.assistant_response.startswith("已导出 👇")

    @pytest.mark.asyncio
    async def test_integration_orchestrator_inference_error_propagation(
        self,
        test_user,
        test_thread_id,
        llm_mocks,
    ):
        """测试推理错误正确传播且不写入脏数据.

        协作场景: InferenceCoordinator.process_with_agent 抛 RuntimeError →
                  process 应传播错误, 不调用 add_conversation_round
        Mock 边界: process_with_agent 抛错 + llm_mocks
        验证重点: RuntimeError 被抛出 / 无对话数据落库
        业务价值: 推理失败时不污染对话历史
        """
        orchestrator = _make_orchestrator()

        with patch.object(
            orchestrator.inference_coordinator,
            "process_with_agent",
            AsyncMock(side_effect=RuntimeError("LLM 服务不可用")),
        ):
            with pytest.raises(RuntimeError, match="LLM 服务不可用"):
                await orchestrator.process(
                    user_input="触发错误的问题",
                    user_id=test_user,
                    thread_id=test_thread_id,
                    processor_config=_make_processor_config(),
                    agent_id=_AGENT_ID,
                )

        await _drain_pinned_bg_tasks()

        conv_svc = await create_conversation_service(
            test_user, test_thread_id, agent_id=_AGENT_ID
        )
        latest = await conv_svc.get_latest_round_number(test_user, test_thread_id)
        assert latest in (0, None), "推理失败时不应落库任何对话轮次"
