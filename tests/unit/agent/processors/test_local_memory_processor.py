"""LocalMemoryProcessor 单元测试.

测试职责: 验证记忆处理器的上下文组装逻辑
测试范围: 首轮处理、非首轮历史组装、错误处理、统计信息
Mock策略: Mock MemoryAssembler (不 Mock DAO), Mock scheduled/conversation service
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agent.memory.local_memory.assembler import MemoryContext
from src.agent.processors.base_processor import MessageContext
from src.agent.processors.local_memory_processor import LocalMemoryProcessor


def _make_agent_config(
    first_turn_prompt: str = "",
    total_char_budget: int = 20000,
    tools: list[str] | None = None,
    optional_tools: list[str] | None = None,
) -> Mock:
    """构造一个最小可用的 agent_config mock."""
    cfg = Mock()
    cfg.first_turn_prompt = first_turn_prompt
    cfg.id = "personal-assistant"
    cfg.memory = Mock()
    cfg.memory.total_char_budget = total_char_budget
    cfg.tools = tools if tools is not None else []
    cfg.optional_tools = optional_tools if optional_tools is not None else []
    return cfg


def _make_memory_ctx(
    history_messages: list | None = None,
    extension: str = "",
) -> MemoryContext:
    """构造一个 MemoryContext."""
    return MemoryContext(
        history_messages=history_messages or [],
        system_prompt_extension=extension,
    )


class TestLocalMemoryProcessor:
    """LocalMemoryProcessor 单元测试."""

    @pytest.fixture
    def processor(self) -> LocalMemoryProcessor:
        """创建 processor 实例 (无需真实配置)."""
        return LocalMemoryProcessor(config=None)

    @pytest.fixture
    def agent_config(self) -> Mock:
        """默认 agent_config (无 first_turn_prompt, 启用 TODO)."""
        return _make_agent_config()

    @pytest.mark.asyncio
    async def test_first_turn_returns_empty_history_and_guidance(
        self,
        processor: LocalMemoryProcessor,
        agent_config: Mock,
        test_user: str,
    ) -> None:
        """首轮对话: history 为空, current_content 含 first_turn_guidance + user_input."""
        agent_config.first_turn_prompt = "请引导用户起名"
        processor_config = {"agent_config": agent_config}

        mock_conv_service = AsyncMock()
        mock_conv_service.get_latest_round_number.return_value = 0

        with patch(
            "src.agent.processors.local_memory_processor.create_conversation_service",
            return_value=mock_conv_service,
        ):
            ctx = await processor.build_messages_context(
                user_input="你好",
                user_id=test_user,
                thread_id="t1",
                agent_id="personal-assistant",
                processor_config=processor_config,
            )

        assert isinstance(ctx, MessageContext)
        assert ctx.history_messages == []
        assert ctx.system_prompt_extension == ""
        assert "<first_turn_guidance>" in ctx.current_content
        assert "请引导用户起名" in ctx.current_content
        assert "<user_input>" in ctx.current_content
        assert "你好" in ctx.current_content
        assert "<current_context>" in ctx.current_content

    @pytest.mark.asyncio
    async def test_non_first_turn_assembles_history_and_extension(
        self,
        processor: LocalMemoryProcessor,
        agent_config: Mock,
        test_user: str,
    ) -> None:
        """非首轮: history 非空, extension 含 pinned, current_content 含 XML 标签."""
        processor_config = {"agent_config": agent_config}

        mock_conv_service = AsyncMock()
        mock_conv_service.get_latest_round_number.return_value = 5

        fake_messages = [
            HumanMessage(content="[过往对话回顾]"),
            AIMessage(content="<conversation_index>早期摘要</conversation_index>"),
            HumanMessage(content="历史用户消息"),
            AIMessage(content="历史助手回复"),
        ]
        fake_ctx = _make_memory_ctx(
            history_messages=fake_messages,
            extension="<pinned_memory>\n用户偏好\n</pinned_memory>",
        )

        mock_assembler = AsyncMock()
        mock_assembler.assemble_memory_context.return_value = fake_ctx

        with (
            patch(
                "src.agent.processors.local_memory_processor.create_conversation_service",
                return_value=mock_conv_service,
            ),
            patch(
                "src.agent.processors.local_memory_processor.MemoryAssembler",
                return_value=mock_assembler,
            ),
            patch.object(
                processor,
                "_get_missed_messages",
                return_value="错过消息1",
            ),
        ):
            ctx = await processor.build_messages_context(
                user_input="今天天气",
                user_id=test_user,
                thread_id="t1",
                agent_id="personal-assistant",
                processor_config=processor_config,
            )

        assert ctx.history_messages == fake_messages
        assert "<pinned_memory>" in ctx.system_prompt_extension
        assert "用户偏好" in ctx.system_prompt_extension

        assert "<current_context>" in ctx.current_content
        assert "<missed_messages>" in ctx.current_content
        assert "错过消息1" in ctx.current_content
        assert "<user_input>" in ctx.current_content
        assert "今天天气" in ctx.current_content

    @pytest.mark.asyncio
    async def test_no_pinned_memory_yields_empty_extension(
        self,
        processor: LocalMemoryProcessor,
        agent_config: Mock,
        test_user: str,
    ) -> None:
        """无置顶记忆时 system_prompt_extension 应为空字符串."""
        processor_config = {"agent_config": agent_config}

        mock_conv_service = AsyncMock()
        mock_conv_service.get_latest_round_number.return_value = 3

        fake_ctx = _make_memory_ctx(
            history_messages=[HumanMessage(content="x"), AIMessage(content="y")],
            extension="",
        )
        mock_assembler = AsyncMock()
        mock_assembler.assemble_memory_context.return_value = fake_ctx

        with (
            patch(
                "src.agent.processors.local_memory_processor.create_conversation_service",
                return_value=mock_conv_service,
            ),
            patch(
                "src.agent.processors.local_memory_processor.MemoryAssembler",
                return_value=mock_assembler,
            ),
            patch.object(processor, "_get_missed_messages", return_value=""),
        ):
            ctx = await processor.build_messages_context(
                user_input="hi",
                user_id=test_user,
                thread_id="t1",
                processor_config=processor_config,
            )

        assert ctx.system_prompt_extension == ""
        assert "<missed_messages>" not in ctx.current_content

    @pytest.mark.asyncio
    async def test_missing_user_id_raises_runtime_error(
        self,
        processor: LocalMemoryProcessor,
        agent_config: Mock,
    ) -> None:
        """缺少 user_id 或 thread_id 应抛 RuntimeError."""
        processor_config = {"agent_config": agent_config}
        with pytest.raises(RuntimeError, match="缺少必要的 user_id 或 thread_id"):
            await processor.build_messages_context(
                user_input="x",
                user_id="",
                thread_id="t1",
                processor_config=processor_config,
            )

    @pytest.mark.asyncio
    async def test_missing_agent_config_raises_runtime_error(
        self,
        processor: LocalMemoryProcessor,
        test_user: str,
    ) -> None:
        """缺少 agent_config 应抛 RuntimeError."""
        with pytest.raises(
            RuntimeError,
            match="LocalMemoryProcessor 需要有效的 agent_config",
        ):
            await processor.build_messages_context(
                user_input="x",
                user_id=test_user,
                thread_id="t1",
                processor_config={},
            )

    @pytest.mark.asyncio
    async def test_first_turn_disabled_when_prompt_empty(
        self,
        processor: LocalMemoryProcessor,
        agent_config: Mock,
        test_user: str,
    ) -> None:
        """first_turn_prompt 为空时不触发首轮逻辑 (零开销, 不查 DB)."""
        agent_config.first_turn_prompt = ""
        processor_config = {"agent_config": agent_config}

        mock_conv_service = AsyncMock()
        fake_ctx = _make_memory_ctx(
            history_messages=[HumanMessage(content="h"), AIMessage(content="a")],
            extension="",
        )
        mock_assembler = AsyncMock()
        mock_assembler.assemble_memory_context.return_value = fake_ctx

        with (
            patch(
                "src.agent.processors.local_memory_processor.create_conversation_service",
                return_value=mock_conv_service,
            ),
            patch(
                "src.agent.processors.local_memory_processor.MemoryAssembler",
                return_value=mock_assembler,
            ),
            patch.object(processor, "_get_missed_messages", return_value=""),
        ):
            ctx = await processor.build_messages_context(
                user_input="hi",
                user_id=test_user,
                thread_id="t1",
                processor_config=processor_config,
            )

        mock_conv_service.get_latest_round_number.assert_not_called()
        assert "<first_turn_guidance>" not in ctx.current_content

    @pytest.mark.asyncio
    async def test_get_processor_stats_returns_local_type(
        self,
        processor: LocalMemoryProcessor,
    ) -> None:
        """统计信息应标识为 local 类型."""
        stats = await processor.get_processor_stats()
        assert stats["processor_type"] == "LocalMemoryProcessor"
        assert stats["memory_type"] == "local"
        assert stats["history_format"] == "messages_array"

    @pytest.mark.asyncio
    async def test_cleanup_completes_without_error(
        self,
        processor: LocalMemoryProcessor,
    ) -> None:
        """cleanup 应无异常完成."""
        await processor.cleanup()


class TestLocalMemoryProcessorCurrentContentBuilder:
    """_build_current_content / _build_first_turn_content 静态方法测试."""

    def test_build_current_content_all_sections(self) -> None:
        """所有部分都存在时应按顺序拼接 XML 标签."""
        content = LocalMemoryProcessor._build_current_content(
            time_str="2026-06-22 10:00:00 CST",
            missed_str="错过",
            user_input="你好",
        )
        assert content.index("<missed_messages>") < content.index("<current_context>")
        assert content.index("<current_context>") < content.index("<user_input>")
        assert "你好" in content

    def test_build_current_content_skips_empty_sections(self) -> None:
        """missed 为空时应跳过对应标签, 仅保留 context + user_input."""
        content = LocalMemoryProcessor._build_current_content(
            time_str="2026-06-22 10:00:00 CST",
            missed_str="",
            user_input="你好",
        )
        assert "<current_context>" in content
        assert "<missed_messages>" not in content
        assert "<user_input>" in content

    def test_build_first_turn_content_structure(self) -> None:
        """首轮内容应含 first_turn_guidance + current_context + user_input."""
        content = LocalMemoryProcessor._build_first_turn_content(
            first_turn_prompt="请引导",
            time_str="2026-06-22 10:00:00 CST",
            user_input="你好",
        )
        assert "<first_turn_guidance>" in content
        assert "请引导" in content
        assert "<current_context>" in content
        assert "<user_input>" in content


# ========== get_prompt_hint ==========


class TestGetPromptHint:
    """LocalMemoryProcessor.get_prompt_hint 条件组装测试."""

    def test_should_return_empty_when_no_agent_config(self):
        """agent_config 为 None 时返回空字符串."""
        processor = LocalMemoryProcessor(None)
        assert processor.get_prompt_hint(None) == ""

    def test_should_always_contain_base_tags(self):
        """始终包含 [过往对话回顾] / <current_context> / <user_input>."""
        processor = LocalMemoryProcessor(None)
        cfg = _make_agent_config()
        hint = processor.get_prompt_hint(cfg)
        assert "[过往对话回顾]" in hint
        assert "<conversation_index>" in hint
        assert "<current_context>" in hint
        assert "<user_input>" in hint
        assert "<current_todos>" not in hint

    def test_should_include_missed_messages_when_scheduled_present(self):
        """定时消息工具存在时含 <missed_messages>."""
        processor = LocalMemoryProcessor(None)
        cfg = _make_agent_config(
            optional_tools=["scheduled_messenger_group"],
        )
        hint = processor.get_prompt_hint(cfg)
        assert "<missed_messages>" in hint

    def test_should_exclude_missed_messages_when_no_scheduled(self):
        """无定时消息工具时不含 <missed_messages>."""
        processor = LocalMemoryProcessor(None)
        cfg = _make_agent_config(
            optional_tools=["web_research"],
        )
        hint = processor.get_prompt_hint(cfg)
        assert "<missed_messages>" not in hint

    def test_tag_order_matches_build_current_content(self):
        """提示词中的标签顺序应与 _build_current_content 一致."""
        processor = LocalMemoryProcessor(None)
        cfg = _make_agent_config(
            optional_tools=["scheduled_messenger_group"],
        )
        hint = processor.get_prompt_hint(cfg)
        assert hint.index("<missed_messages>") < hint.index("<current_context>")
        assert hint.index("<current_context>") < hint.index("<user_input>")
