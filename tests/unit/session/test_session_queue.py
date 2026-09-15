"""SessionMessageQueue 单元测试.

覆盖消息队列核心逻辑:
- submit / submit_streaming 入队行为
- processor_loop 单条/合并处理
- _execute_agent / _execute_agent_streaming 执行与文件链接拼接
- 异常路径下的 future/sink 收尾

Mock 边界:
- agent 的 process_message / process_message_stream / finalize_conversation
- chat_helpers.allocate_round_number / prepare_image_attachments
- message_formatting.build_file_links
- context.set_user_context / get_user_context_or_none / reset_user_context
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.session.session_queue import QueuedMessage, SessionMessageQueue


@pytest.fixture(autouse=True)
def _clean_session_queue_instances() -> None:
    """每个测试前清理单例缓存, 避免测试间状态污染."""
    SessionMessageQueue._instances.clear()
    yield
    SessionMessageQueue._instances.clear()


class _ErrorAsyncStream:
    """迭代时抛出异常的异步流."""

    def __aiter__(self) -> _ErrorAsyncStream:
        return self

    async def __anext__(self) -> str:
        raise RuntimeError("流式失败")


class _AsyncTokenStream:
    """用于 AsyncMock 返回的可迭代异步流."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens

    def __aiter__(self) -> _AsyncTokenStream:
        return self

    async def __anext__(self) -> str:
        if not self._tokens:
            raise StopAsyncIteration
        return self._tokens.pop(0)


@pytest.fixture
def mock_agent() -> AsyncMock:
    """创建模拟 Agent, 默认返回固定响应."""
    agent = AsyncMock()
    agent.process_message = AsyncMock(return_value="模拟响应")
    agent.finalize_conversation = AsyncMock(return_value=None)
    agent.process_message_stream = Mock(
        return_value=_AsyncTokenStream(["token1", "token2"])
    )
    return agent


@pytest.fixture
def queue_key(test_user: str, test_thread_id: str) -> str:
    """生成测试用队列 key."""
    return f"{test_user}:{test_thread_id}:test_agent"


class TestSessionMessageQueueGet:
    """测试 get 类方法."""

    def test_get_should_create_new_instance(self, test_user: str, test_thread_id: str):
        """测试 get: 首次调用应创建新实例."""
        queue = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")

        assert isinstance(queue, SessionMessageQueue)
        assert queue._key == f"{test_user}:{test_thread_id}:test_agent"

    def test_get_should_return_same_instance(self, test_user: str, test_thread_id: str):
        """测试 get: 相同参数应返回同一实例."""
        queue1 = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")
        queue2 = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")

        assert queue1 is queue2


class TestSessionMessageQueueSubmit:
    """测试 submit 入队与单条处理."""

    @pytest.mark.asyncio
    async def test_submit_should_return_future_and_process_message(
        self,
        mock_agent: AsyncMock,
        test_user: str,
        test_thread_id: str,
    ):
        """测试 submit: 应返回 future 并在处理后得到响应."""
        # Arrange
        queue = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.session.session_queue.build_file_links", return_value=""),
            patch(
                "src.core.context.set_user_context",
                return_value="ctx_token",
            ),
            patch(
                "src.core.context.get_user_context_or_none",
                return_value=None,
            ),
            patch("src.core.context.reset_user_context") as mock_reset_ctx,
        ):
            # Act
            future = await queue.submit(
                user_input="你好",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
            )
            response = await asyncio.wait_for(future, timeout=1.0)

            # Assert
            assert response == "模拟响应"
            mock_agent.process_message.assert_awaited_once()
            mock_reset_ctx.assert_called_once_with("ctx_token")

    @pytest.mark.asyncio
    async def test_submit_with_exported_files_should_append_file_links(
        self,
        mock_agent: AsyncMock,
        test_user: str,
        test_thread_id: str,
    ):
        """测试 submit: 有导出文件时应拼接文件链接到响应."""
        # Arrange
        queue = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")
        mock_ctx = Mock()
        mock_ctx.exported_files = [{"url": "http://example.com/file.txt"}]

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch(
                "src.session.session_queue.build_file_links",
                return_value="\n---\n[file](http://example.com/file.txt)",
            ),
            patch(
                "src.core.context.set_user_context",
                return_value="ctx_token",
            ),
            patch(
                "src.core.context.get_user_context_or_none",
                return_value=mock_ctx,
            ),
            patch("src.core.context.reset_user_context"),
        ):
            # Act
            future = await queue.submit(
                user_input="你好",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
            )
            response = await asyncio.wait_for(future, timeout=1.0)

            # Assert
            assert "file" in response
            assert "http://example.com/file.txt" in response


class TestSessionMessageQueueDocumentDatas:
    """测试 document_datas (纯文本文档) 的传递与合并."""

    @pytest.mark.asyncio
    async def test_submit_with_documents_should_store_and_pass_attachments(
        self,
        mock_agent: AsyncMock,
        test_user: str,
        test_thread_id: str,
    ):
        """submit 携带 document_datas: 保存文档, attachment_infos 传给 agent."""
        queue = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")
        doc_dto = Mock()
        doc_dto.file_type = "document"

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch(
                "src.session.session_queue.prepare_document_attachments",
                AsyncMock(return_value=[doc_dto]),
            ) as mock_prepare_doc,
            patch("src.session.session_queue.build_file_links", return_value=""),
            patch("src.core.context.set_user_context", return_value="ctx_token"),
            patch("src.core.context.get_user_context_or_none", return_value=None),
            patch("src.core.context.reset_user_context"),
        ):
            future = await queue.submit(
                user_input="总结文件",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
                document_datas=[{"filename": "a.md", "content": "A"}],
            )
            response = await asyncio.wait_for(future, timeout=1.0)

        assert response == "模拟响应"
        mock_prepare_doc.assert_awaited_once()
        call_kwargs = mock_prepare_doc.await_args.kwargs
        assert call_kwargs["document_datas"] == [{"filename": "a.md", "content": "A"}]
        # agent 收到合并后的 attachment_infos
        agent_kwargs = mock_agent.process_message.await_args.kwargs
        assert agent_kwargs["attachment_infos"] == [doc_dto]

    @pytest.mark.asyncio
    async def test_merged_documents_should_extend_across_batch(
        self,
        mock_agent: AsyncMock,
        test_user: str,
        test_thread_id: str,
    ):
        """合并处理: 多条消息的 document_datas 累加传给存储层."""
        queue = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch(
                "src.session.session_queue.prepare_document_attachments",
                AsyncMock(return_value=[]),
            ) as mock_prepare_doc,
            patch("src.session.session_queue.build_file_links", return_value=""),
            patch("src.core.context.set_user_context", return_value="ctx_token"),
            patch("src.core.context.get_user_context_or_none", return_value=None),
            patch("src.core.context.reset_user_context"),
        ):
            f1 = await queue.submit(
                user_input="第一条",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
                document_datas=[{"filename": "a.md", "content": "A"}],
            )
            f2 = await queue.submit(
                user_input="第二条",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
                document_datas=[{"filename": "b.md", "content": "B"}],
            )
            await asyncio.wait_for(f1, timeout=1.0)
            await asyncio.wait_for(f2, timeout=1.0)

        merged_docs = mock_prepare_doc.await_args.kwargs["document_datas"]
        assert [d["filename"] for d in merged_docs] == ["a.md", "b.md"]

    @pytest.mark.asyncio
    async def test_submit_streaming_with_documents_should_pass_attachments(
        self,
        mock_agent: AsyncMock,
        test_user: str,
        test_thread_id: str,
    ):
        """流式路径同样传递 document_datas 与 attachment_infos."""
        queue = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")
        doc_dto = Mock()

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch(
                "src.session.session_queue.prepare_document_attachments",
                AsyncMock(return_value=[doc_dto]),
            ),
            patch("src.session.session_queue.build_file_links", return_value=""),
            patch("src.core.context.set_user_context", return_value="ctx_token"),
            patch("src.core.context.get_user_context_or_none", return_value=None),
            patch("src.core.context.reset_user_context"),
        ):
            tokens = []
            iterator = await queue.submit_streaming(
                user_input="总结文件",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
                document_datas=[{"filename": "a.md", "content": "A"}],
            )
            async for token in iterator:
                tokens.append(token)

        assert tokens == ["token1", "token2"]
        agent_kwargs = mock_agent.process_message_stream.call_args.kwargs
        assert agent_kwargs["attachment_infos"] == [doc_dto]


class TestSessionMessageQueueMergedProcessing:
    """测试合并处理多条消息."""

    @pytest.mark.asyncio
    async def test_two_submits_should_merge_and_return_response_to_last(
        self,
        mock_agent: AsyncMock,
        test_user: str,
        test_thread_id: str,
    ):
        """测试两条消息应合并为一次 agent 调用, 仅最后一条得到响应."""
        # Arrange
        queue = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")
        mock_agent.process_message = AsyncMock(return_value="合并响应")

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.session.session_queue.build_file_links", return_value=""),
            patch("src.core.context.set_user_context", return_value="ctx"),
            patch("src.core.context.get_user_context_or_none", return_value=None),
            patch("src.core.context.reset_user_context"),
        ):
            # Act
            future1 = await queue.submit(
                user_input="第一条",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
            )
            future2 = await queue.submit(
                user_input="第二条",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
            )

            response1 = await asyncio.wait_for(future1, timeout=1.0)
            response2 = await asyncio.wait_for(future2, timeout=1.0)

            # Assert
            assert response1 is None
            assert response2 == "合并响应"
            mock_agent.process_message.assert_awaited_once()
            call_kwargs = mock_agent.process_message.await_args.kwargs
            assert "第一条" in call_kwargs["message"]
            assert "第二条" in call_kwargs["message"]

    @pytest.mark.asyncio
    async def test_merged_processing_exception_should_set_exception_on_all(
        self,
        mock_agent: AsyncMock,
        test_user: str,
        test_thread_id: str,
    ):
        """测试合并处理异常时所有 future 都应收到异常."""
        # Arrange
        queue = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")
        mock_agent.process_message = AsyncMock(side_effect=RuntimeError("合并失败"))

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.core.context.set_user_context", return_value="ctx"),
            patch("src.core.context.get_user_context_or_none", return_value=None),
            patch("src.core.context.reset_user_context"),
        ):
            # Act
            future1 = await queue.submit(
                user_input="第一条",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
            )
            future2 = await queue.submit(
                user_input="第二条",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
            )

            # Assert
            with pytest.raises(RuntimeError, match="合并失败"):
                await asyncio.wait_for(future1, timeout=1.0)
            with pytest.raises(RuntimeError, match="合并失败"):
                await asyncio.wait_for(future2, timeout=1.0)


class TestSessionMessageQueueStreaming:
    """测试流式提交与消费."""

    @pytest.mark.asyncio
    async def test_submit_streaming_should_yield_tokens(
        self,
        mock_agent: AsyncMock,
        test_user: str,
        test_thread_id: str,
    ):
        """测试 submit_streaming: 应逐 token 产出响应."""
        # Arrange
        queue = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")

        mock_agent.process_message_stream = Mock(
            return_value=_AsyncTokenStream(["hello", "world"])
        )

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.session.session_queue.build_file_links", return_value=""),
            patch("src.core.context.set_user_context", return_value="ctx"),
            patch("src.core.context.get_user_context_or_none", return_value=None),
            patch("src.core.context.reset_user_context"),
        ):
            # Act
            iterator = await queue.submit_streaming(
                user_input="流式测试",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
            )
            tokens = [token async for token in iterator]

            # Assert
            assert tokens == ["hello", "world"]
            mock_agent.finalize_conversation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_submit_streaming_empty_response_should_use_fallback(
        self,
        mock_agent: AsyncMock,
        test_user: str,
        test_thread_id: str,
    ):
        """测试 submit_streaming: 空流响应时应使用兜底文案."""
        # Arrange
        queue = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")

        mock_agent.process_message_stream = Mock(return_value=_AsyncTokenStream([]))
        captured_response: str | None = None

        def _capture_finalize(**kwargs: Any) -> None:
            nonlocal captured_response
            captured_response = kwargs.get("response")

        mock_agent.finalize_conversation = AsyncMock(side_effect=_capture_finalize)

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.core.context.set_user_context", return_value="ctx"),
            patch("src.core.context.get_user_context_or_none", return_value=None),
            patch("src.core.context.reset_user_context"),
        ):
            # Act
            iterator = await queue.submit_streaming(
                user_input="空响应测试",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
            )
            tokens = [token async for token in iterator]

            # Assert
            assert tokens == []
            assert captured_response == "返回响应为空"

    @pytest.mark.asyncio
    async def test_submit_streaming_exception_should_close_sink_without_tokens(
        self,
        mock_agent: AsyncMock,
        test_user: str,
        test_thread_id: str,
    ):
        """测试 submit_streaming: 流式执行异常应静默关闭 sink, 不产出 token."""
        # Arrange
        queue = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")

        mock_agent.process_message_stream = Mock(return_value=_ErrorAsyncStream())

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.core.context.set_user_context", return_value="ctx"),
            patch("src.core.context.get_user_context_or_none", return_value=None),
            patch("src.core.context.reset_user_context") as mock_reset_ctx,
        ):
            # Act
            iterator = await queue.submit_streaming(
                user_input="异常测试",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
            )
            tokens = [token async for token in iterator]

            # Assert: 异常被内部捕获, 消费端正常结束且无 token 产出
            assert tokens == []
            mock_agent.finalize_conversation.assert_not_awaited()
            mock_reset_ctx.assert_called_once_with("ctx")

    @pytest.mark.asyncio
    async def test_merged_streaming_should_only_last_yield_tokens(
        self,
        mock_agent: AsyncMock,
        test_user: str,
        test_thread_id: str,
    ):
        """测试合并流式: 仅最后一条消息产出 token, 其余静默关闭."""
        # Arrange
        queue = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")

        mock_agent.process_message_stream = Mock(
            return_value=_AsyncTokenStream(["merged"])
        )

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.core.context.set_user_context", return_value="ctx"),
            patch("src.core.context.get_user_context_or_none", return_value=None),
            patch("src.core.context.reset_user_context"),
        ):
            # Act
            iterator1 = await queue.submit_streaming(
                user_input="第一条流式",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
            )
            iterator2 = await queue.submit_streaming(
                user_input="第二条流式",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
            )

            tokens1 = [token async for token in iterator1]
            tokens2 = [token async for token in iterator2]

            # Assert
            assert tokens1 == []
            assert tokens2 == ["merged"]
            mock_agent.process_message_stream.assert_called_once()


class TestSessionMessageQueueSignalAbsorbed:
    """测试 _signal_absorbed 静态方法."""

    @pytest.mark.asyncio
    async def test_signal_absorbed_streaming_should_put_none(self):
        """测试 _signal_absorbed: 流式消息应放入 None 哨兵."""
        # Arrange
        sink: asyncio.Queue[str | None] = asyncio.Queue()
        msg = QueuedMessage(
            user_input="test",
            image_datas=[],
            timezone="Asia/Shanghai",
            streaming=True,
            token_sink=sink,
        )

        # Act
        SessionMessageQueue._signal_absorbed(msg)

        # Assert
        assert sink.get_nowait() is None

    @pytest.mark.asyncio
    async def test_signal_absorbed_non_streaming_should_set_none_future(self):
        """测试 _signal_absorbed: 非流式消息应设置 future 结果为 None."""
        # Arrange
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        msg = QueuedMessage(
            user_input="test",
            image_datas=[],
            timezone="Asia/Shanghai",
            response_future=future,
        )

        # Act
        SessionMessageQueue._signal_absorbed(msg)

        # Assert
        assert await asyncio.wait_for(future, timeout=0.5) is None


class TestSessionMessageQueueProcessorLoop:
    """测试 processor_loop 边界行为."""

    @pytest.mark.asyncio
    async def test_processor_should_stop_after_processing_single_message(
        self,
        mock_agent: AsyncMock,
        test_user: str,
        test_thread_id: str,
    ):
        """测试 processor: 处理完单条消息后应自动停止."""
        # Arrange
        queue = SessionMessageQueue.get(test_user, test_thread_id, "test_agent")

        with (
            patch(
                "src.session.session_queue.allocate_round_number",
                AsyncMock(return_value=1),
            ),
            patch(
                "src.session.session_queue.prepare_image_attachments",
                AsyncMock(return_value=[]),
            ),
            patch("src.session.session_queue.build_file_links", return_value=""),
            patch("src.core.context.set_user_context", return_value="ctx"),
            patch("src.core.context.get_user_context_or_none", return_value=None),
            patch("src.core.context.reset_user_context"),
        ):
            # Act
            future = await queue.submit(
                user_input="测试",
                image_datas=[],
                timezone="Asia/Shanghai",
                agent=mock_agent,
            )
            await asyncio.wait_for(future, timeout=1.0)

            # Assert: 给事件循环一次调度机会, 确保 processor task 已结束
            await asyncio.sleep(0)
            assert queue._processing is False
            assert queue._processor is None or queue._processor.done()
