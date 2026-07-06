"""InferenceCoordinator流式处理单元测试.

测试src.agent.processors.inference_coordinator的流式功能.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage

from src.agent.processors.inference_coordinator import InferenceCoordinator
from src.core.streaming import StreamContent


def _make_chunk(content: str) -> tuple[AIMessageChunk, dict]:
    """构造 stream_mode="messages" 格式的chunk元组."""
    return (AIMessageChunk(content=content), {"langgraph_node": "model"})


def _drain_streaming(chunks: list[str]) -> str:
    """模拟连续喂入 chunk, 收集 filter_think_tags_streaming 所有 yield 的文本."""
    output: list[str] = []
    in_block = False
    buffer = ""
    for chunk in chunks:
        result = InferenceCoordinator._filter_think_tags_streaming(
            chunk, in_block, buffer,
        )
        if isinstance(result, tuple):
            in_block, buffer = result
            continue
        if result:
            output.append(result)
    return "".join(output)


class TestInferenceCoordinatorStream:
    """InferenceCoordinator流式处理测试."""

    @pytest.fixture
    def coordinator(self):
        """创建InferenceCoordinator实例."""
        return InferenceCoordinator(config=None)

    @pytest.fixture
    def mock_agent(self):
        """Mock LangChain Agent."""
        agent = AsyncMock()

        # Mock astream()方法 - 返回 stream_mode="messages" 格式
        async def mock_astream(input_data, config=None, **kwargs):
            yield _make_chunk("Hello")
            yield _make_chunk(" World")
            yield _make_chunk("!")

        agent.astream = mock_astream
        return agent

    @pytest.mark.asyncio
    async def test_process_with_agent_stream_success(self, coordinator, mock_agent):
        """测试流式处理成功."""
        # Arrange
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()

        # 包装astream为MagicMock以便验证调用
        mock_agent.astream = MagicMock(wraps=mock_agent.astream)

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            # Act
            chunks = []
            async for chunk in coordinator.process_with_agent_stream(
                user_content="你好",
                system_prompt="You are helpful",
                llm_config={"model": "gpt-3.5-turbo"},
                user_id="test_user",
                thread_id="test_thread",
            ):
                chunks.append(chunk)

            # Assert
            assert chunks == ["Hello", " World", "!"]
            mock_agent.astream.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_with_agent_stream_with_model_creation(self, coordinator):
        """测试流式处理包含模型创建."""
        # Arrange
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()

        # Mock create_agent
        mock_agent = AsyncMock()

        async def mock_astream(input_data, config=None, **kwargs):
            yield _make_chunk("Response")

        mock_agent.astream = mock_astream

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            # Act
            chunks = []
            async for chunk in coordinator.process_with_agent_stream(
                user_content="Test",
                system_prompt="You are helpful",
                llm_config={"model": "openai:gpt-3.5-turbo"},
                user_id="test_user",
                thread_id="test_thread",
            ):
                chunks.append(chunk)

            # Assert
            assert chunks == ["Response"]

    @pytest.mark.asyncio
    async def test_process_with_agent_stream_error_handling(self, coordinator):
        """测试流式处理错误处理."""
        # Arrange
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()
        mock_agent = AsyncMock()

        # Mock astream抛出异常
        async def mock_astream_error(input_data, config=None, **kwargs):
            if False:
                yield
            raise RuntimeError("LLM调用失败")

        mock_agent.astream = mock_astream_error

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            # Act & Assert
            with pytest.raises(RuntimeError, match="流式处理失败"):
                async for _ in coordinator.process_with_agent_stream(
                    user_content="Test",
                    system_prompt="You are helpful",
                    llm_config={"model": "gpt-3.5-turbo"},
                    user_id="test_user",
                    thread_id="test_thread",
                ):
                    pass

    @pytest.mark.asyncio
    async def test_process_with_agent_stream_empty_chunks(
        self, coordinator, mock_agent
    ):
        """测试流式处理返回空内容块."""
        # Arrange
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()

        # Mock agent返回空内容
        async def mock_astream_empty(input_data, config=None, **kwargs):
            yield _make_chunk("")
            yield _make_chunk("")
            yield _make_chunk("Hello")

        mock_agent.astream = mock_astream_empty

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            # Act
            chunks = []
            async for chunk in coordinator.process_with_agent_stream(
                user_content="Test",
                system_prompt="You are helpful",
                llm_config={"model": "gpt-3.5-turbo"},
                user_id="test_user",
                thread_id="test_thread",
            ):
                if chunk is not None:
                    chunks.append(chunk)

            # Assert - 应该过滤掉空内容
            assert chunks == ["Hello"]

    @pytest.mark.asyncio
    async def test_process_with_agent_stream_unicode_content(
        self, coordinator, mock_agent
    ):
        """测试流式处理Unicode内容."""
        # Arrange
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()

        # Mock agent返回中文内容
        async def mock_astream_unicode(input_data, config=None, **kwargs):
            yield _make_chunk("你好")
            yield _make_chunk("世界")

        mock_agent.astream = mock_astream_unicode

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            # Act
            chunks = []
            async for chunk in coordinator.process_with_agent_stream(
                user_content="你好",
                system_prompt="你是帮助者",
                llm_config={"model": "gpt-3.5-turbo"},
                user_id="test_user",
                thread_id="test_thread",
            ):
                chunks.append(chunk)

            # Assert
            assert chunks == ["你好", "世界"]

    @pytest.mark.asyncio
    async def test_process_with_agent_stream_preserves_config(self, coordinator):
        """测试流式处理保留配置."""
        # Arrange
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()
        mock_agent = AsyncMock()

        # 记录接收到的参数
        received_kwargs = {}

        async def mock_astream(input_data, config=None, **kwargs):
            received_kwargs.update(kwargs)
            received_kwargs["config"] = config
            yield _make_chunk("OK")

        mock_agent.astream = mock_astream

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            # Act
            chunks = []
            async for chunk in coordinator.process_with_agent_stream(
                user_content="Test",
                system_prompt="You are helpful",
                llm_config={
                    "model": "openai:gpt-3.5-turbo",
                    "temperature": 0.7,
                    "max_tokens": 1000,
                },
                user_id="test_user",
                thread_id="test_thread",
            ):
                chunks.append(chunk)

            # Assert
            assert chunks == ["OK"]
            # 验证 stream_mode="messages" 被传递
            assert received_kwargs.get("stream_mode") == "messages"

    @pytest.mark.asyncio
    async def test_process_with_agent_stream_filters_tool_messages(self, coordinator):
        """测试流式处理过滤ToolMessage, 只保留AIMessageChunk."""
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()
        mock_agent = AsyncMock()

        # 模拟包含工具调用的完整流程:
        # 1. LLM输出tool_call (AIMessageChunk content为空)
        # 2. 工具执行完成 (ToolMessage)
        # 3. LLM基于工具结果生成最终回复 (AIMessageChunk tokens)
        async def mock_astream_with_tools(input_data, config=None, **kwargs):
            yield (AIMessageChunk(content=""), {"langgraph_node": "model"})
            yield (
                ToolMessage(content="搜索结果: 卫健委食谱...", tool_call_id="call_1"),
                {"langgraph_node": "tools"},
            )
            yield _make_chunk("根据")
            yield _make_chunk("搜索结果")
            yield _make_chunk("...")

        mock_agent.astream = mock_astream_with_tools

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            chunks = []
            async for chunk in coordinator.process_with_agent_stream(
                user_content="搜索卫健委食谱",
                system_prompt="You are helpful",
                llm_config={"model": "gpt-3.5-turbo"},
                user_id="test_user",
                thread_id="test_thread",
            ):
                chunks.append(chunk)

            # 只有AIMessageChunk的内容被yield, ToolMessage被过滤
            assert chunks == ["根据", "搜索结果", "..."]
            assert "搜索结果: 卫健委食谱" not in "".join(
                str(c) for c in chunks if isinstance(c, str)
            )

    @pytest.mark.asyncio
    async def test_process_with_agent_stream_emits_tool_call_html(self, coordinator):
        """测试流式处理在 tool_calls 完全组装后记录, ToolMessage 到达时输出 done HTML."""
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()
        mock_agent = AsyncMock()

        async def mock_astream_with_tool_calls(input_data, config=None, **kwargs):
            # 1. AIMessageChunk with fully assembled tool_calls
            yield (
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "web_search",
                            "args": {"query": "Python异步"},
                            "id": "call_abc",
                            "type": "tool_call",
                        },
                    ],
                ),
                {"langgraph_node": "model"},
            )
            # 2. ToolMessage (工具执行完成)
            yield (
                ToolMessage(
                    content="搜索结果: Python异步编程指南", tool_call_id="call_abc"
                ),
                {"langgraph_node": "tools"},
            )
            # 3. LLM 最终回复
            yield _make_chunk("根据")
            yield _make_chunk("搜索结果")

        mock_agent.astream = mock_astream_with_tool_calls

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            chunks = []
            async for chunk in coordinator.process_with_agent_stream(
                user_content="搜索Python异步",
                system_prompt="You are helpful",
                llm_config={"model": "gpt-3.5-turbo"},
                user_id="test_user",
                thread_id="test_thread",
            ):
                chunks.append(chunk)

            stream_contents = [c for c in chunks if isinstance(c, StreamContent)]
            text_chunks = [c for c in chunks if isinstance(c, str)]

            # 只有 1 个 StreamContent: done 标签 (不发送 start 标签,
            # 因为 delta 追加模式无法正确替换 done=false 为 done=true)
            assert len(stream_contents) == 1

            done = stream_contents[0]
            assert done.display_only is True
            assert 'name="web_search"' in done.content
            assert 'done="true"' in done.content
            assert "Python异步编程指南" in done.content
            # 真实参数 (非空 {})
            assert "Python异步" in done.content

            assert text_chunks == ["根据", "搜索结果"]

    @pytest.mark.asyncio
    async def test_tool_call_display_disabled(self, coordinator):
        """测试禁用工具调用显示时不输出 HTML."""
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()
        mock_agent = AsyncMock()

        async def mock_astream_with_tool_calls(input_data, config=None, **kwargs):
            yield (
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "create_todo",
                            "args": {"title": "测试任务"},
                            "id": "call_1",
                            "type": "tool_call",
                        },
                    ],
                ),
                {"langgraph_node": "model"},
            )
            yield (
                ToolMessage(content="Task created", tool_call_id="call_1"),
                {"langgraph_node": "tools"},
            )
            yield _make_chunk("好的")

        mock_agent.astream = mock_astream_with_tool_calls

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
            patch.object(
                InferenceCoordinator,
                "_is_tool_call_display_enabled",
                return_value=False,
            ),
        ):
            chunks = []
            async for chunk in coordinator.process_with_agent_stream(
                user_content="创建TODO",
                system_prompt="You are helpful",
                llm_config={"model": "gpt-3.5-turbo"},
                user_id="test_user",
                thread_id="test_thread",
            ):
                chunks.append(chunk)

            stream_contents = [c for c in chunks if isinstance(c, StreamContent)]
            assert len(stream_contents) == 0
            assert chunks == ["好的"]

    @pytest.mark.asyncio
    async def test_tool_call_display_memory_isolation(self, coordinator):
        """测试工具调用 HTML 的记忆隔离: StreamContent.display_only=True 不应入记忆."""
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()
        mock_agent = AsyncMock()

        async def mock_astream_with_tool(input_data, config=None, **kwargs):
            yield (
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_memories",
                            "args": {"query": "记忆"},
                            "id": "call_x",
                            "type": "tool_call",
                        },
                    ],
                ),
                {"langgraph_node": "model"},
            )
            yield (
                ToolMessage(content="找到3条记忆", tool_call_id="call_x"),
                {"langgraph_node": "tools"},
            )
            yield _make_chunk("根据")
            yield _make_chunk("记忆")

        mock_agent.astream = mock_astream_with_tool

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            memory_parts = []
            display_parts = []

            async for chunk in coordinator.process_with_agent_stream(
                user_content="搜索记忆",
                system_prompt="You are helpful",
                llm_config={"model": "gpt-3.5-turbo"},
                user_id="test_user",
                thread_id="test_thread",
            ):
                if isinstance(chunk, StreamContent):
                    display_parts.append(chunk.content)
                    if not chunk.display_only:
                        memory_parts.append(chunk.content)
                else:
                    memory_parts.append(chunk)
                    display_parts.append(chunk)

            # 记忆中只有文本, 无 HTML
            assert memory_parts == ["根据", "记忆"]
            assert "<details" not in "".join(memory_parts)

            # 展示中包含 HTML (done 标签)
            assert any("<details" in d for d in display_parts)
            # done 标签包含真实参数 (取 done="true" 的标签)
            done_html = next(d for d in display_parts if "<details" in d and 'done="true"' in d)
            assert "记忆" in done_html


class TestThinkTagFiltering:
    """流式路径 <think/> 标签过滤测试."""

    @pytest.mark.parametrize(
        "content,in_think,buffer,expected",
        [
            ("你好世界", False, "", "你好世界"),
            ("normal text", False, "", "normal text"),
            ("<think\n>reasoning</think\n>", False, "", ""),
            ("<think\n>reasoning</think\n>visible", False, "", "visible"),
            ("before<think\n>reason</think\n>after", False, "", "beforeafter"),
            ("<think\n>partial", False, "", (True, "partial")),
            ("more text", True, "partial", (True, "partialmore text")),
            ("end</think\n>visible", True, "partialend", "visible"),
            ("<think\n>a</think\n>b<think\n>c</think\n>d", False, "", "bd"),
            # 有前缀文本时未闭合, 应返回前缀
            ("before<think\n>secret", False, "", "before"),
            # 闭合后无剩余内容, 应重置状态
            ("</think\n>", True, "", (False, "")),
        ],
    )
    def test_filter_think_tags_streaming(
        self, content, in_think, buffer, expected
    ):
        """测试各种 <think/> 标签场景."""
        result = InferenceCoordinator._filter_think_tags_streaming(
            content, in_think, buffer,
        )
        if isinstance(expected, tuple):
            assert isinstance(result, tuple)
            assert result[0] == expected[0]
            assert result[1] == expected[1]
        else:
            assert result == expected


class TestStreamingCrossChunk:
    """跨 chunk 连续喂入的 think 标签过滤测试."""

    # 注: 本类不测试 "<think> 开标签被切断在 chunk 边界" 的情况
    # (如 ["<thi", "nk>..."]). 当前实现不做标签前缀碎片缓冲:
    # 流式实测不触发泄露, 且碎片本身不含 reasoning 语义;
    # 加缓冲会破坏 think_buffer 语义并需改调用方契约, 复杂度收益比差.

    def test_isolated_close_in_single_chunk(self):
        """孤立闭合标签与其左侧 reasoning 在同一 chunk 内时, 丢弃左侧."""
        assert _drain_streaming(["reasoning</think>visible"]) == "visible"

    def test_isolated_close_split_across_chunks(self):
        """孤立闭合标签跨 chunk 时: 已 yield 的 reasoning 无法回收, 但至少闭合标签本身被吞掉."""
        assert _drain_streaming(["reasoning", "</think>visible"]) == "reasoningvisible"

    def test_content_before_open_tag(self):
        """开标签前有正文时, 正文应保留, think 块内容应过滤."""
        assert (
            _drain_streaming(["hello ", "<think>x</think>", " world"])
            == "hello  world"
        )

    def test_no_tags_passthrough(self):
        """无标签文本应完整透传."""
        assert _drain_streaming(["just ", "normal ", "text"]) == "just normal text"

    def test_thinking_content_before_open_tag(self):
        """<thinking> 开标签前有正文时, 正文应保留."""
        assert (
            _drain_streaming(
                ["prefix ", "<thinking>x</thinking>", " suffix"],
            )
            == "prefix  suffix"
        )


class TestToolCallChunkTextFilteringInStream:
    """流式处理中工具调用 chunk 文本过滤: 含tool_call的chunk文本全部跳过."""

    @pytest.fixture
    def coordinator(self):
        return InferenceCoordinator(config=None)

    @pytest.mark.asyncio
    async def test_tool_call_chunk_text_unconditionally_filtered(self, coordinator):
        """测试含tool_call的chunk文本被无条件跳过, 无论长短."""
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()
        mock_agent = AsyncMock()

        async def mock_astream(input_data, config=None, **kwargs):
            # LLM 同时输出短中间文本 + tool_call
            yield (
                AIMessageChunk(
                    content="让我搜一下确认～",
                    tool_call_chunks=[
                        {"id": "call_1", "name": "web_search", "index": 0, "args": ""},
                    ],
                ),
                {"langgraph_node": "model"},
            )
            yield (
                ToolMessage(
                    content="搜索结果: 质数78498个", tool_call_id="call_1"
                ),
                {"langgraph_node": "tools"},
            )
            # LLM 输出长文本 + tool_call (第二工具调用)
            substantial = "根据数学定理，质数计数函数π(n)近似为n/ln(n)，可以推导出100万以内的质数约为78498个"
            yield (
                AIMessageChunk(
                    content=substantial,
                    tool_call_chunks=[
                        {"id": "call_2", "name": "web_search", "index": 0, "args": ""},
                    ],
                ),
                {"langgraph_node": "model"},
            )
            yield (
                ToolMessage(content="确认结果", tool_call_id="call_2"),
                {"langgraph_node": "tools"},
            )
            # 最终响应 (无 tool_call)
            yield _make_chunk("根据搜索结果")
            yield _make_chunk("，共有78498个质数")

        mock_agent.astream = mock_astream

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            chunks = []
            async for chunk in coordinator.process_with_agent_stream(
                user_content="100万以内有多少质数",
                system_prompt="You are helpful",
                llm_config={"model": "gpt-3.5-turbo"},
                user_id="test_user",
                thread_id="test_thread",
            ):
                chunks.append(chunk)

            text_chunks = [c for c in chunks if isinstance(c, str)]
            stream_contents = [c for c in chunks if isinstance(c, StreamContent)]

            # 中间文本全部被跳过 (短文本 + 长文本)
            assert "让我搜一下确认" not in "".join(text_chunks)
            assert "质数计数函数" not in "".join(text_chunks)
            # 只有最终响应文本
            assert text_chunks == ["根据搜索结果", "，共有78498个质数"]
            # 有 2 个 StreamContent: 2个工具调用的 done 标签
            # (不发送 start 标签, delta 追加模式无法正确替换)
            assert len(stream_contents) == 2

    @pytest.mark.asyncio
    async def test_think_tags_filtered_in_stream(self, coordinator):
        """测试流式处理中 <think/> 标签被过滤."""
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()
        mock_agent = AsyncMock()

        async def mock_astream(input_data, config=None, **kwargs):
            yield _make_chunk("<think\n>让我分析一下这个问题")
            yield _make_chunk("用户问的是质数数量</think\n>")
            yield _make_chunk("100万以内共有78498个质数")

        mock_agent.astream = mock_astream

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            chunks = []
            async for chunk in coordinator.process_with_agent_stream(
                user_content="质数数量",
                system_prompt="You are helpful",
                llm_config={"model": "deepseek-v3"},
                user_id="test_user",
                thread_id="test_thread",
            ):
                chunks.append(chunk)

            combined = "".join(chunks)
            assert "<think" not in combined
            assert "让我分析" not in combined
            assert "78498个质数" in combined


class TestToolNodeLLMLeakFiltering:
    """工具内部嵌套 LLM 输出泄露过滤测试.

    验证 langgraph stream_mode="messages" 输出的工具内部 LLM token
    (如 _llm_tool_filter 的 Qwen3-4B、专家工具的 Gemini) 被来源过滤拦截,
    不会泄露给用户前端.
    """

    @pytest.fixture
    def coordinator(self):
        return InferenceCoordinator(config=None)

    @pytest.mark.asyncio
    async def test_tool_node_plain_text_filtered(self, coordinator):
        """工具内部 LLM 的纯文本输出 (tools 节点) 被过滤, 不泄露给用户."""
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()
        mock_agent = AsyncMock()

        async def mock_astream(input_data, config=None, **kwargs):
            # 主 LLM 决定调用 search_available_tools
            yield (
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "id": "call_1",
                            "name": "search_available_tools",
                            "index": 0,
                            "args": "",
                        },
                    ],
                ),
                {"langgraph_node": "model"},
            )
            # 工具内部 Qwen3-4B 输出 (tools 节点, 必须被过滤)
            yield (
                AIMessageChunk(content='{"relevant": [1]}'),
                {"langgraph_node": "tools"},
            )
            # 工具返回结果 (ToolMessage, 正常处理为展示标签)
            yield (
                ToolMessage(
                    content='{"success": true, "matched_tools": [...]}',
                    tool_call_id="call_1",
                ),
                {"langgraph_node": "tools"},
            )
            # 主 LLM 最终答案
            yield _make_chunk("这是最终答案")

        mock_agent.astream = mock_astream

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            chunks = []
            async for chunk in coordinator.process_with_agent_stream(
                user_content="搜索工具",
                system_prompt="You are helpful",
                llm_config={"model": "gpt-3.5-turbo"},
                user_id="test_user",
                thread_id="test_thread",
            ):
                chunks.append(chunk)

            text_chunks = [c for c in chunks if isinstance(c, str)]

            # 工具内部 LLM 输出被过滤, 不泄露
            assert '{"relevant"' not in "".join(text_chunks)
            # 主 LLM 最终答案正常输出
            assert "这是最终答案" in "".join(text_chunks)

    @pytest.mark.asyncio
    async def test_expert_tool_nested_llm_filtered(self, coordinator):
        """专家工具内嵌套 agent (如 Gemini) 的搜索结果被过滤, 不泄露给用户."""
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()
        mock_agent = AsyncMock()

        async def mock_astream(input_data, config=None, **kwargs):
            # 主 LLM 调用专家工具
            yield (
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "id": "call_1",
                            "name": "web_research",
                            "index": 0,
                            "args": "",
                        },
                    ],
                ),
                {"langgraph_node": "model"},
            )
            # 专家工具内 Gemini 搜索中间 token + 结果 JSON (tools 节点)
            yield (
                AIMessageChunk(content="根据搜索结果, "),
                {"langgraph_node": "tools"},
            )
            yield (
                AIMessageChunk(
                    content='{"groundingChunks": [{"web": {"uri": "https://example.com"}}]}',
                ),
                {"langgraph_node": "tools"},
            )
            # 工具返回最终结果
            yield (
                ToolMessage(
                    content='{"result": "研究完成"}',
                    tool_call_id="call_1",
                ),
                {"langgraph_node": "tools"},
            )
            # 主 LLM 最终答案
            yield _make_chunk("综合研究结果: ")

        mock_agent.astream = mock_astream

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            chunks = []
            async for chunk in coordinator.process_with_agent_stream(
                user_content="研究一下",
                system_prompt="You are helpful",
                llm_config={"model": "gpt-3.5-turbo"},
                user_id="test_user",
                thread_id="test_thread",
            ):
                chunks.append(chunk)

            text_chunks = [c for c in chunks if isinstance(c, str)]

            # Gemini 搜索中间 token 和原始结果 JSON 被过滤
            assert "根据搜索结果" not in "".join(text_chunks)
            assert "groundingChunks" not in "".join(text_chunks)
            # 主 LLM 最终答案正常输出
            assert "综合研究结果" in "".join(text_chunks)

    @pytest.mark.asyncio
    async def test_metadata_missing_passes_through(self, coordinator):
        """metadata 缺失 langgraph_node 时放行, 兼容非标准环境, 避免静默切断."""
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock()
        mock_embeddings = AsyncMock()
        mock_agent = AsyncMock()

        async def mock_astream(input_data, config=None, **kwargs):
            # metadata 不含 langgraph_node (None 兜底放行)
            yield (AIMessageChunk(content="正常输出"), {})

        mock_agent.astream = mock_astream

        with (
            patch(
                "src.agent.processors.inference_coordinator.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.agent.processors.inference_coordinator.create_agent",
                return_value=mock_agent,
            ),
        ):
            chunks = []
            async for chunk in coordinator.process_with_agent_stream(
                user_content="测试",
                system_prompt="You are helpful",
                llm_config={"model": "gpt-3.5-turbo"},
                user_id="test_user",
                thread_id="test_thread",
            ):
                chunks.append(chunk)

            text_chunks = [c for c in chunks if isinstance(c, str)]
            assert text_chunks == ["正常输出"]
