"""ProcessorOrchestrator单元测试.

测试职责: 验证处理器总协调器的核心功能逻辑
测试范围: 初始化、process流程、流式处理、finalize、记忆存储、错误处理
Mock策略: Mock外部依赖(LocalMemoryProcessor/InferenceCoordinator/存储服务)，保留Orchestrator核心逻辑
测试价值: 确保处理器总协调器的稳定性和可靠性
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.agent.processors.processor_orchestrator import ProcessorOrchestrator
from src.core.streaming import StreamContent

try:
    from tests.decorators import quick_test
except ImportError:

    def quick_test(func):
        return func


def _mock_conv_service(round_num: int = 1, latest: int = 0) -> Mock:
    """创建mock对话服务."""
    svc = Mock()
    svc.allocate_round_number = AsyncMock(return_value=round_num)
    svc.get_latest_round_number = AsyncMock(return_value=latest)
    svc.get_conversation_by_round = AsyncMock(return_value=Mock())
    return svc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config() -> dict[str, str]:
    """创建Mock配置."""
    return {"model": {"llm": "local:qwen3.5:9b"}, "cache": {"ttl": 3600}}


@pytest.fixture
def mock_memory_processor() -> Mock:
    """创建Mock记忆处理器 - 带完整接口.

    使用spec限制自动属性，确保hasattr只对显式设置的属性返回True。
    """
    from src.agent.processors.base_processor import MessageContext

    processor = Mock(
        spec=[
            "initialize",
            "cleanup",
            "build_messages_context",
            "get_processor_stats",
            "get_prompt_hint",
        ],
    )
    processor.initialize = AsyncMock()
    processor.cleanup = AsyncMock()
    # build_messages_context 返回空 history 时, orchestrator 透传 history=None
    processor.build_messages_context = AsyncMock(
        return_value=MessageContext(
            history_messages=[],
            current_content="Mock built content",
            system_prompt_extension="",
        ),
    )
    processor.get_processor_stats = AsyncMock(
        return_value={"processor_type": "MockProcessor"}
    )
    processor.get_prompt_hint = Mock(return_value="")
    # get_or_create_conversation_memory 未列出，hasattr返回False
    return processor


@pytest.fixture
def mock_inference_coordinator() -> Mock:
    """创建Mock推理协调器."""
    coordinator = Mock()
    coordinator.process_with_agent = AsyncMock(
        return_value=("AI response", {"tokens": 100})
    )
    coordinator.process_with_agent_stream = Mock()
    return coordinator


@pytest.fixture
def orchestrator(
    mock_config: dict[str, str],
    mock_memory_processor: Mock,
    mock_inference_coordinator: Mock,
) -> ProcessorOrchestrator:
    """创建已初始化的协调器(Mock所有外部依赖)."""
    with (
        patch(
            "src.agent.processors.processor_orchestrator.LocalMemoryProcessor",
            return_value=mock_memory_processor,
        ),
        patch(
            "src.agent.processors.processor_orchestrator.InferenceCoordinator",
            return_value=mock_inference_coordinator,
        ),
    ):
        orch = ProcessorOrchestrator(mock_config, "local")

    # 保持Mock引用以便验证
    orch._mock_memory = mock_memory_processor
    orch._mock_inference = mock_inference_coordinator
    return orch


@pytest.fixture
def basic_processor_config() -> dict[str, Any]:
    """基础处理器配置."""
    return {
        "system_prompt": "You are a helpful assistant.",
        "llm_config": {"model": "test-model"},
    }


@pytest.fixture
def processor_config_with_agent() -> dict[str, Any]:
    """包含agent_config的处理器配置."""
    agent_config = Mock()
    agent_config.model_id = "test-model-v2"
    agent_config.agent_id = "agent_001"
    agent_config.llm_config = {"temperature": 0.7}
    return {
        "system_prompt": "You are a helpful assistant.",
        "agent_config": agent_config,
    }


# ---------------------------------------------------------------------------
# 初始化测试
# ---------------------------------------------------------------------------


class TestProcessorOrchestratorInit:
    """初始化相关测试."""

    @quick_test
    def test_init_unknown_memory_type_falls_back(
        self, mock_config: dict[str, str]
    ) -> None:
        """测试未知memory_type回退到local."""
        with (
            patch(
                "src.agent.processors.processor_orchestrator.LocalMemoryProcessor"
            ) as mock_cls,
            patch("src.agent.processors.processor_orchestrator.InferenceCoordinator"),
        ):
            mock_cls.return_value = Mock()
            orch = ProcessorOrchestrator(mock_config, "redis")
            # memory_type保持原值，但内部使用local处理器
            assert orch.memory_type == "redis"
            assert mock_cls.call_count >= 1


# ---------------------------------------------------------------------------
# initialize 异步测试
# ---------------------------------------------------------------------------


class TestInitialize:
    """initialize 方法测试."""

    @pytest.mark.asyncio
    @quick_test
    async def test_initialize_success(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试成功初始化."""
        await orchestrator.initialize()
        orchestrator._mock_memory.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    @quick_test
    async def test_initialize_failure_raises_runtime_error(
        self, mock_config: dict[str, str]
    ) -> None:
        """测试初始化失败抛出RuntimeError."""
        with (
            patch(
                "src.agent.processors.processor_orchestrator.LocalMemoryProcessor"
            ) as mock_cls,
            patch("src.agent.processors.processor_orchestrator.InferenceCoordinator"),
        ):
            mock_proc = Mock()
            mock_proc.initialize = AsyncMock(
                side_effect=ConnectionError("DB connection failed")
            )
            mock_cls.return_value = mock_proc

            orch = ProcessorOrchestrator(mock_config, "local")
            with pytest.raises(RuntimeError, match="处理器总协调器初始化失败"):
                await orch.initialize()


# ---------------------------------------------------------------------------
# process 方法测试
# ---------------------------------------------------------------------------


class TestProcess:
    """process 方法测试."""

    @pytest.mark.asyncio
    @quick_test
    async def test_process_no_config_raises_valueerror(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试未传递processor_config时抛出ValueError."""
        with pytest.raises(RuntimeError, match="处理器总协调器失败"):
            await orchestrator.process(
                user_input="hello",
                user_id="u1",
                thread_id="t1",
            )

    @pytest.mark.asyncio
    @quick_test
    async def test_process_config_none_raises_cause_valueerror(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试processor_config=None时,根异常是ValueError."""
        with pytest.raises(RuntimeError) as exc_info:
            await orchestrator.process(
                user_input="hello",
                user_id="u1",
                thread_id="t1",
                processor_config=None,
            )
        assert "处理器配置未传递" in str(exc_info.value.__cause__)

    @pytest.mark.asyncio
    @quick_test
    async def test_process_success_basic(
        self,
        orchestrator: ProcessorOrchestrator,
        basic_processor_config: dict[str, Any],
    ) -> None:
        """测试基本成功处理流程."""
        response, stats, conv_data = await orchestrator.process(
            user_input="Hello",
            user_id="u1",
            thread_id="t1",
            processor_config=basic_processor_config,
        )

        assert response == "AI response"
        assert stats is not None
        assert stats["orchestrator_stats"]["memory_type"] == "local"
        assert stats["orchestrator_stats"]["processing_success"] is True
        assert stats["inference_stats"] == {"tokens": 100}
        assert stats["memory_stats"] == {"processor_type": "MockProcessor"}
        assert (
            conv_data is None
        )  # memory_processor没有get_or_create_conversation_memory

    @pytest.mark.asyncio
    @quick_test
    async def test_process_with_agent_config_llm_override(
        self,
        orchestrator: ProcessorOrchestrator,
        processor_config_with_agent: dict[str, Any],
    ) -> None:
        """测试有agent_config时LLM配置构建."""
        await orchestrator.process(
            user_input="Hello",
            user_id="u1",
            thread_id="t1",
            processor_config=processor_config_with_agent,
        )

        call_kwargs = orchestrator._mock_inference.process_with_agent.call_args.kwargs
        assert call_kwargs["llm_config"]["model"] == "test-model-v2"
        assert call_kwargs["llm_config"]["temperature"] == 0.7

    @pytest.mark.asyncio
    @quick_test
    async def test_process_with_agent_config_no_llm_config(
        self,
        orchestrator: ProcessorOrchestrator,
    ) -> None:
        """测试agent_config存在但无llm_config属性时使用默认model_id."""
        agent_config = Mock()
        agent_config.model_id = "base-model"
        del agent_config.llm_config

        config = {"system_prompt": "test", "agent_config": agent_config}
        await orchestrator.process(
            user_input="Hello",
            user_id="u1",
            thread_id="t1",
            processor_config=config,
        )

        call_kwargs = orchestrator._mock_inference.process_with_agent.call_args.kwargs
        assert call_kwargs["llm_config"] == {"model": "base-model"}

    @pytest.mark.asyncio
    @quick_test
    async def test_process_inference_error_raises(
        self,
        orchestrator: ProcessorOrchestrator,
        basic_processor_config: dict[str, Any],
    ) -> None:
        """测试推理协调器失败时抛出异常."""
        orchestrator._mock_inference.process_with_agent = AsyncMock(
            side_effect=ConnectionError("API timeout")
        )

        with pytest.raises(RuntimeError, match="处理器总协调器失败"):
            await orchestrator.process(
                user_input="Hello",
                user_id="u1",
                thread_id="t1",
                processor_config=basic_processor_config,
            )

    @pytest.mark.asyncio
    @quick_test
    async def test_process_with_image_datas(
        self,
        orchestrator: ProcessorOrchestrator,
        basic_processor_config: dict[str, Any],
    ) -> None:
        """测试传递图片数据."""
        images = [{"data": b"fake", "mime_type": "image/png"}]
        await orchestrator.process(
            user_input="See image",
            user_id="u1",
            thread_id="t1",
            processor_config=basic_processor_config,
            image_datas=images,
        )

        call_kwargs = orchestrator._mock_inference.process_with_agent.call_args.kwargs
        assert call_kwargs["image_datas"] == images

    @pytest.mark.asyncio
    @quick_test
    async def test_process_with_memory_stats_error(
        self,
        orchestrator: ProcessorOrchestrator,
        basic_processor_config: dict[str, Any],
    ) -> None:
        """测试获取记忆统计失败时不影响主流程."""
        orchestrator._mock_memory.get_processor_stats = AsyncMock(
            side_effect=RuntimeError("stats error")
        )

        response, stats, _ = await orchestrator.process(
            user_input="Hello",
            user_id="u1",
            thread_id="t1",
            processor_config=basic_processor_config,
        )

        assert response == "AI response"
        assert stats["memory_stats"] == {}

    @pytest.mark.asyncio
    @quick_test
    async def test_process_conversation_memory_without_add_method(
        self,
        orchestrator: ProcessorOrchestrator,
        processor_config_with_agent: dict[str, Any],
    ) -> None:
        """测试对话记忆实例缺少add_conversation_round方法时不崩溃."""
        conv_memory = Mock(spec=[])  # 空spec, 没有add_conversation_round
        orchestrator._mock_memory.get_or_create_conversation_memory = AsyncMock(
            return_value=conv_memory
        )

        response, stats, conv_data = await orchestrator.process(
            user_input="Hello",
            user_id="u1",
            thread_id="t1",
            processor_config=processor_config_with_agent,
        )

        assert response == "AI response"
        assert conv_data is None

    @pytest.mark.asyncio
    @quick_test
    async def test_process_conversation_memory_error_raises(
        self,
        orchestrator: ProcessorOrchestrator,
        processor_config_with_agent: dict[str, Any],
    ) -> None:
        """测试对话记忆更新失败时抛出RuntimeError."""
        conv_memory = Mock()
        conv_memory.add_conversation_round = AsyncMock(
            side_effect=RuntimeError("DB error")
        )
        orchestrator._mock_memory.get_or_create_conversation_memory = AsyncMock(
            return_value=conv_memory
        )

        with (
            patch(
                "src.agent.processors.processor_orchestrator.create_conversation_service",
                return_value=_mock_conv_service(1),
            ),
            pytest.raises(RuntimeError, match="处理器总协调器失败"),
        ):
            await orchestrator.process(
                user_input="Hello",
                user_id="u1",
                thread_id="t1",
                processor_config=processor_config_with_agent,
            )


# ---------------------------------------------------------------------------
# process_stream 方法测试
# ---------------------------------------------------------------------------


class TestProcessStream:
    """process_stream 流式处理测试."""

    @pytest.mark.asyncio
    @quick_test
    async def test_stream_no_config_raises(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试流式处理未传config时抛出异常."""
        with pytest.raises(RuntimeError, match="处理器总协调器流式处理失败"):
            chunks = []
            async for chunk in orchestrator.process_stream(
                user_input="hello", user_id="u1", thread_id="t1"
            ):
                chunks.append(chunk)

    @pytest.mark.asyncio
    @quick_test
    async def test_stream_config_none_raises(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试流式处理config=None时抛出异常."""
        with pytest.raises(RuntimeError):
            chunks = []
            async for chunk in orchestrator.process_stream(
                user_input="hello",
                user_id="u1",
                thread_id="t1",
                processor_config=None,
            ):
                chunks.append(chunk)

    @pytest.mark.asyncio
    @quick_test
    async def test_stream_success(
        self,
        orchestrator: ProcessorOrchestrator,
        basic_processor_config: dict[str, Any],
    ) -> None:
        """测试成功流式处理."""

        async def fake_stream(**kwargs: Any) -> AsyncIterator[str]:
            yield "Hello"
            yield " World"

        orchestrator._mock_inference.process_with_agent_stream = fake_stream

        chunks = []
        async for chunk in orchestrator.process_stream(
            user_input="Hi",
            user_id="u1",
            thread_id="t1",
            processor_config=basic_processor_config,
        ):
            chunks.append(chunk)

        assert chunks == ["Hello", " World"]

    @pytest.mark.asyncio
    @quick_test
    async def test_stream_calls_build_messages_context(
        self,
        orchestrator: ProcessorOrchestrator,
        basic_processor_config: dict[str, Any],
    ) -> None:
        """测试流式处理调用了build_messages_context (新架构入口)."""

        async def fake_stream(**kwargs: Any) -> AsyncIterator[str]:
            yield "ok"

        orchestrator._mock_inference.process_with_agent_stream = fake_stream

        chunks = []
        async for chunk in orchestrator.process_stream(
            user_input="Hi",
            user_id="u1",
            thread_id="t1",
            agent_id="a1",
            processor_config=basic_processor_config,
            timezone="UTC",
        ):
            chunks.append(chunk)

        orchestrator._mock_memory.build_messages_context.assert_awaited_once_with(
            user_input="Hi",
            user_id="u1",
            thread_id="t1",
            agent_id="a1",
            processor_config=basic_processor_config,
            timezone="UTC",
        )

    @pytest.mark.asyncio
    @quick_test
    async def test_stream_with_agent_config_llm(
        self,
        orchestrator: ProcessorOrchestrator,
        processor_config_with_agent: dict[str, Any],
    ) -> None:
        """测试流式处理中agent_config的LLM配置构建."""
        received_kwargs: dict[str, Any] = {}

        async def fake_stream(**kwargs: Any) -> AsyncIterator[str]:
            received_kwargs.update(kwargs)
            yield "ok"

        orchestrator._mock_inference.process_with_agent_stream = fake_stream

        chunks = []
        async for chunk in orchestrator.process_stream(
            user_input="Hi",
            user_id="u1",
            thread_id="t1",
            processor_config=processor_config_with_agent,
        ):
            chunks.append(chunk)

        assert received_kwargs["llm_config"]["model"] == "test-model-v2"
        assert received_kwargs["llm_config"]["temperature"] == 0.7

    @pytest.mark.asyncio
    @quick_test
    async def test_stream_with_image_datas(
        self,
        orchestrator: ProcessorOrchestrator,
        basic_processor_config: dict[str, Any],
    ) -> None:
        """测试流式处理传递图片数据."""
        received_kwargs: dict[str, Any] = {}

        async def fake_stream(**kwargs: Any) -> AsyncIterator[str]:
            received_kwargs.update(kwargs)
            yield "ok"

        orchestrator._mock_inference.process_with_agent_stream = fake_stream
        images = [{"data": b"img", "mime_type": "image/jpeg"}]

        chunks = []
        async for chunk in orchestrator.process_stream(
            user_input="See this",
            user_id="u1",
            thread_id="t1",
            processor_config=basic_processor_config,
            image_datas=images,
        ):
            chunks.append(chunk)

        assert received_kwargs["image_datas"] == images

    @pytest.mark.asyncio
    @quick_test
    async def test_stream_inference_error_raises(
        self,
        orchestrator: ProcessorOrchestrator,
        basic_processor_config: dict[str, Any],
    ) -> None:
        """测试流式推理失败时抛出异常."""

        async def failing_stream(**kwargs: Any) -> AsyncIterator[str]:
            raise ConnectionError("Stream failed")
            yield  # 使其成为生成器

        orchestrator._mock_inference.process_with_agent_stream = failing_stream

        with pytest.raises(RuntimeError, match="处理器总协调器流式处理失败"):
            chunks = []
            async for chunk in orchestrator.process_stream(
                user_input="Hi",
                user_id="u1",
                thread_id="t1",
                processor_config=basic_processor_config,
            ):
                chunks.append(chunk)

    @pytest.mark.asyncio
    @quick_test
    async def test_stream_yields_stream_content(
        self,
        orchestrator: ProcessorOrchestrator,
        basic_processor_config: dict[str, Any],
    ) -> None:
        """测试流式处理能返回StreamContent对象."""

        async def fake_stream(**kwargs: Any) -> AsyncIterator[str | StreamContent]:
            yield "text chunk"
            yield StreamContent(content="<tool>html</tool>", display_only=True)

        orchestrator._mock_inference.process_with_agent_stream = fake_stream

        chunks: list[str | StreamContent] = []
        async for chunk in orchestrator.process_stream(
            user_input="Hi",
            user_id="u1",
            thread_id="t1",
            processor_config=basic_processor_config,
        ):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0] == "text chunk"
        assert isinstance(chunks[1], StreamContent)
        assert chunks[1].display_only is True


# ---------------------------------------------------------------------------
# finalize_conversation 方法测试
# ---------------------------------------------------------------------------


class TestFinalizeConversation:
    """finalize_conversation 方法测试."""

    @pytest.mark.asyncio
    @quick_test
    async def test_finalize_no_memory_support(
        self,
        orchestrator: ProcessorOrchestrator,
        basic_processor_config: dict[str, Any],
    ) -> None:
        """测试记忆处理器不支持对话记忆时返回None."""
        result = await orchestrator.finalize_conversation(
            user_input="Hello",
            response_content="Hi there",
            user_id="u1",
            thread_id="t1",
            processor_config=basic_processor_config,
            agent_id="a1",
        )
        assert result is None

    @pytest.mark.asyncio
    @quick_test
    async def test_finalize_conversation_memory_no_add_method(
        self,
        orchestrator: ProcessorOrchestrator,
        basic_processor_config: dict[str, Any],
    ) -> None:
        """测试对话记忆实例缺少add_conversation_round方法时返回None."""
        conv_memory = Mock(spec=[])  # 空spec
        orchestrator._mock_memory.get_or_create_conversation_memory = AsyncMock(
            return_value=conv_memory
        )

        result = await orchestrator.finalize_conversation(
            user_input="Hello",
            response_content="Hi there",
            user_id="u1",
            thread_id="t1",
            processor_config=basic_processor_config,
            agent_id="a1",
        )
        assert result is None

    @pytest.mark.asyncio
    @quick_test
    async def test_finalize_with_agent_config(
        self,
        orchestrator: ProcessorOrchestrator,
        processor_config_with_agent: dict[str, Any],
    ) -> None:
        """测试使用agent_config的agent_id."""
        conv_memory = Mock()
        conv_memory.add_conversation_round = AsyncMock()
        orchestrator._mock_memory.get_or_create_conversation_memory = AsyncMock(
            return_value=conv_memory
        )

        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            return_value=_mock_conv_service(1, 0),
        ):
            result = await orchestrator.finalize_conversation(
                user_input="Hello",
                response_content="Hi",
                user_id="u1",
                thread_id="t1",
                processor_config=processor_config_with_agent,
            )

        assert result is not None
        assert result.agent_id == "agent_001"

    @pytest.mark.asyncio
    @quick_test
    async def test_finalize_no_agent_id_raises(
        self,
        orchestrator: ProcessorOrchestrator,
    ) -> None:
        """测试缺少agent_id时抛出异常."""
        conv_memory = Mock()
        conv_memory.add_conversation_round = AsyncMock()
        orchestrator._mock_memory.get_or_create_conversation_memory = AsyncMock(
            return_value=conv_memory
        )

        with pytest.raises(RuntimeError, match="完成对话处理失败"):
            await orchestrator.finalize_conversation(
                user_input="Hello",
                response_content="Hi",
                user_id="u1",
                thread_id="t1",
                processor_config={},
                agent_id=None,
            )

    @pytest.mark.asyncio
    @quick_test
    async def test_finalize_confirm_round_failure_does_not_crash(
        self,
        orchestrator: ProcessorOrchestrator,
        basic_processor_config: dict[str, Any],
    ) -> None:
        """测试轮次号确认失败时不影响主流程."""
        conv_memory = Mock()
        conv_memory.add_conversation_round = AsyncMock()
        orchestrator._mock_memory.get_or_create_conversation_memory = AsyncMock(
            return_value=conv_memory
        )

        mock_conv_svc = _mock_conv_service(1, 0)
        mock_conv_svc.get_conversation_by_round = AsyncMock(
            side_effect=RuntimeError("confirm failed")
        )

        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            return_value=mock_conv_svc,
        ):
            result = await orchestrator.finalize_conversation(
                user_input="Hello",
                response_content="Hi",
                user_id="u1",
                thread_id="t1",
                processor_config=basic_processor_config,
                agent_id="a1",
            )

        assert result is not None
        assert result.round_number == 1


# ---------------------------------------------------------------------------
# _build_conversation_data 测试
# ---------------------------------------------------------------------------


class TestBuildConversationData:
    """_build_conversation_data 方法测试."""

    @pytest.mark.asyncio
    @quick_test
    async def test_build_basic(self, orchestrator: ProcessorOrchestrator) -> None:
        """测试基本ConversationData构建."""
        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            return_value=_mock_conv_service(5, 3),
        ):
            data = await orchestrator._build_conversation_data(
                user_input="Hello",
                response_content="Hi",
                user_id="u1",
                thread_id="t1",
                agent_id="a1",
            )

        assert data.user_id == "u1"
        assert data.thread_id == "t1"
        assert data.assistant_response == "Hi"
        assert data.round_number == 5
        assert data.agent_id == "a1"
        assert "[" in data.user_message
        assert "Hello" in data.user_message

    @pytest.mark.asyncio
    @quick_test
    async def test_build_uses_preallocated_round(
        self,
        orchestrator: ProcessorOrchestrator,
    ) -> None:
        """测试预分配轮次号不会再次分配."""
        with (
            patch.object(
                orchestrator,
                "_allocate_round_number_simple",
                new_callable=AsyncMock,
            ) as mock_allocate,
            patch.object(
                orchestrator,
                "_detect_round_number_anomaly",
                new_callable=AsyncMock,
            ),
        ):
            data = await orchestrator._build_conversation_data(
                user_input="Hello",
                response_content="Hi",
                user_id="u1",
                thread_id="t1",
                agent_id="a1",
                round_number=9,
            )

        assert data.round_number == 9
        mock_allocate.assert_not_awaited()

    @pytest.mark.asyncio
    @quick_test
    async def test_build_with_timezone(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试自定义时区的消息构建."""
        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            return_value=_mock_conv_service(1, 0),
        ):
            data = await orchestrator._build_conversation_data(
                user_input="Hello",
                response_content="Hi",
                user_id="u1",
                thread_id="t1",
                agent_id="a1",
                timezone="America/New_York",
            )

        assert "Hello" in data.user_message

    @pytest.mark.asyncio
    @quick_test
    async def test_build_no_agent_id_raises(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试无agent_id时抛出ValueError."""
        with pytest.raises(ValueError, match="agent_id 不能为空"):
            await orchestrator._build_conversation_data(
                user_input="Hello",
                response_content="Hi",
                user_id="u1",
                thread_id="t1",
                agent_id=None,
            )

    @pytest.mark.asyncio
    @quick_test
    async def test_build_allocate_round_fails(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试轮次号分配失败时抛出异常."""
        mock_svc = Mock()
        mock_svc.allocate_round_number = AsyncMock(
            side_effect=RuntimeError("allocation failed")
        )
        with (
            patch(
                "src.agent.processors.processor_orchestrator.create_conversation_service",
                return_value=mock_svc,
            ),
            pytest.raises(RuntimeError, match="allocation failed"),
        ):
            await orchestrator._build_conversation_data(
                user_input="Hello",
                response_content="Hi",
                user_id="u1",
                thread_id="t1",
                agent_id="a1",
            )


# ---------------------------------------------------------------------------
# _resolve_agent_id 测试
# ---------------------------------------------------------------------------


class TestResolveAgentId:
    """_resolve_agent_id 静态方法测试."""

    @quick_test
    def test_resolve_from_agent_config(self) -> None:
        """测试从agent_config获取agent_id."""
        agent_config = Mock()
        agent_config.agent_id = "config_agent"
        result = ProcessorOrchestrator._resolve_agent_id(agent_config, None, "test")
        assert result == "config_agent"

    @quick_test
    def test_resolve_from_explicit_param(self) -> None:
        """测试从显式参数获取agent_id."""
        result = ProcessorOrchestrator._resolve_agent_id(None, "explicit_agent", "test")
        assert result == "explicit_agent"

    @quick_test
    def test_resolve_agent_config_priority(self) -> None:
        """测试agent_config优先于显式参数."""
        agent_config = Mock()
        agent_config.agent_id = "config_agent"
        result = ProcessorOrchestrator._resolve_agent_id(
            agent_config, "explicit_agent", "test"
        )
        assert result == "config_agent"

    @quick_test
    def test_resolve_none_raises(self) -> None:
        """测试两者都为空时抛出ValueError."""
        with pytest.raises(ValueError, match="agent_id 不能为空"):
            ProcessorOrchestrator._resolve_agent_id(None, None, "test_context")

    @quick_test
    def test_resolve_empty_agent_config_agent_id(self) -> None:
        """测试agent_config存在但agent_id为空字符串时使用显式参数."""
        agent_config = Mock()
        agent_config.agent_id = ""
        result = ProcessorOrchestrator._resolve_agent_id(
            agent_config, "fallback", "test"
        )
        assert result == "fallback"

    @quick_test
    def test_resolve_agent_config_without_attribute(self) -> None:
        """测试agent_config无agent_id属性时使用显式参数."""
        agent_config = Mock(spec=[])  # 无属性
        result = ProcessorOrchestrator._resolve_agent_id(
            agent_config, "fallback", "test"
        )
        assert result == "fallback"


# ---------------------------------------------------------------------------
# _detect_round_number_anomaly 测试
# ---------------------------------------------------------------------------


class TestDetectRoundNumberAnomaly:
    """_detect_round_number_anomaly 方法测试."""

    @pytest.mark.asyncio
    @quick_test
    async def test_no_anomaly(self, orchestrator: ProcessorOrchestrator) -> None:
        """测试无异常跳跃."""
        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            return_value=_mock_conv_service(1, 5),
        ):
            result = await orchestrator._detect_round_number_anomaly(
                "u1", "t1", 8, "a1"
            )

        assert result is False

    @pytest.mark.asyncio
    @quick_test
    async def test_anomaly_detected(self, orchestrator: ProcessorOrchestrator) -> None:
        """测试检测到异常跳跃(>10)."""
        mock_svc = Mock()
        mock_svc.get_latest_round_number = AsyncMock(return_value=1)
        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            return_value=mock_svc,
        ):
            result = await orchestrator._detect_round_number_anomaly(
                "u1", "t1", 20, "a1"
            )

        assert result is True

    @pytest.mark.asyncio
    @quick_test
    async def test_boundary_jump_10_not_anomaly(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试跳跃等于10不视为异常."""
        mock_svc = Mock()
        mock_svc.get_latest_round_number = AsyncMock(return_value=5)
        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            return_value=mock_svc,
        ):
            result = await orchestrator._detect_round_number_anomaly(
                "u1", "t1", 15, "a1"
            )

        assert result is False

    @pytest.mark.asyncio
    @quick_test
    async def test_detection_failure_returns_false(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试检测失败时返回False(不影响主流程)."""
        # create_conversation_service本身失败
        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            side_effect=RuntimeError("unavailable"),
        ):
            result = await orchestrator._detect_round_number_anomaly(
                "u1", "t1", 5, "a1"
            )

        assert result is False


# ---------------------------------------------------------------------------
# get_processor_stats / get_memory_type / get_memory_processor / cleanup
# ---------------------------------------------------------------------------


class TestUtilityMethods:
    """工具方法测试."""

    @pytest.mark.asyncio
    @quick_test
    async def test_get_processor_stats_success(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试获取处理器统计信息."""
        stats = await orchestrator.get_processor_stats()

        assert stats["orchestrator_type"] == "ProcessorOrchestrator"
        assert stats["memory_type"] == "local"
        assert stats["memory_processor_stats"] == {"processor_type": "MockProcessor"}
        assert stats["inference_coordinator_available"] is True

    @pytest.mark.asyncio
    @quick_test
    async def test_get_processor_stats_no_stats_method(
        self, mock_config: dict[str, str]
    ) -> None:
        """测试记忆处理器无get_processor_stats方法."""
        with (
            patch(
                "src.agent.processors.processor_orchestrator.LocalMemoryProcessor"
            ) as mock_cls,
            patch("src.agent.processors.processor_orchestrator.InferenceCoordinator"),
        ):
            mock_proc = Mock(spec=["initialize", "cleanup"])
            mock_cls.return_value = mock_proc

            orch = ProcessorOrchestrator(mock_config)
            stats = await orch.get_processor_stats()

        assert stats["memory_processor_stats"] == {}

    @pytest.mark.asyncio
    @quick_test
    async def test_get_processor_stats_error(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试获取统计信息失败时返回错误信息."""
        orchestrator._mock_memory.get_processor_stats = AsyncMock(
            side_effect=RuntimeError("stats crash")
        )

        stats = await orchestrator.get_processor_stats()
        assert "error" in stats

    @pytest.mark.asyncio
    @quick_test
    async def test_cleanup_success(self, orchestrator: ProcessorOrchestrator) -> None:
        """测试成功清理资源."""
        await orchestrator.cleanup()
        orchestrator._mock_memory.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    @quick_test
    async def test_cleanup_no_cleanup_method(self, mock_config: dict[str, str]) -> None:
        """测试记忆处理器无cleanup方法时不崩溃."""
        with (
            patch(
                "src.agent.processors.processor_orchestrator.LocalMemoryProcessor"
            ) as mock_cls,
            patch("src.agent.processors.processor_orchestrator.InferenceCoordinator"),
        ):
            mock_proc = Mock(spec=["initialize"])
            mock_cls.return_value = mock_proc

            orch = ProcessorOrchestrator(mock_config)
            await orch.cleanup()  # 不应抛异常

    @pytest.mark.asyncio
    @quick_test
    async def test_cleanup_error_logged(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试清理失败时记录日志但不崩溃."""
        orchestrator._mock_memory.cleanup = AsyncMock(
            side_effect=RuntimeError("cleanup error")
        )
        await orchestrator.cleanup()


# ---------------------------------------------------------------------------
# _allocate_round_number_simple 测试
# ---------------------------------------------------------------------------


class TestAllocateRoundNumber:
    """_allocate_round_number_simple 方法测试."""

    @pytest.mark.asyncio
    @quick_test
    async def test_allocate_success(self, orchestrator: ProcessorOrchestrator) -> None:
        """测试成功分配轮次号."""
        mock_svc = Mock()
        mock_svc.allocate_round_number = AsyncMock(return_value=7)
        with patch(
            "src.agent.processors.processor_orchestrator.create_conversation_service",
            return_value=mock_svc,
        ):
            result = await orchestrator._allocate_round_number_simple("u1", "t1", "a1")

        assert result == 7

    @pytest.mark.asyncio
    @quick_test
    async def test_allocate_failure_raises(
        self, orchestrator: ProcessorOrchestrator
    ) -> None:
        """测试分配失败时抛出异常."""
        mock_svc = Mock()
        mock_svc.allocate_round_number = AsyncMock(side_effect=RuntimeError("db error"))
        with (
            patch(
                "src.agent.processors.processor_orchestrator.create_conversation_service",
                return_value=mock_svc,
            ),
            pytest.raises(RuntimeError, match="db error"),
        ):
            await orchestrator._allocate_round_number_simple("u1", "t1", "a1")
