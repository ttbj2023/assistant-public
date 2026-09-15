"""KbImageInjectMiddleware 单元测试 - 多模态主模型直注知识库原图.

测试范围:
1. 非多模态/未提供模型: 透传 handler (走工具内部 vision 转述)
2. 多模态 + kb_read_image: 短路 handler, Command 注入 文本ToolMessage +
   携带 image_url 块的 HumanMessage (provider 安全形态)
3. 其他工具: 透传
4. ref 非法/图片缺失/图片超大: 降级 handler
5. 并行 kb_read_image: 模型调用前保持 tool_calls 响应连续(DeepSeek 等
   provider 要求 assistant 的多个 tool_calls 由连续 ToolMessage 立即响应,
   直注 HumanMessage 不得穿插其间)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware import ModelRequest, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from src.tools.middleware._image_inject import KbImageInjectMiddleware


def _make_request(tool_name: str, args: dict | None = None) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": tool_name, "args": args or {}, "id": "tc_1"},
        tool=MagicMock(),
        state={},
        runtime=MagicMock(),
    )


def _make_model_request(messages: list[Any]) -> ModelRequest:
    return ModelRequest(
        model=MagicMock(),
        messages=messages,
        system_message=None,
        tool_choice=None,
        tools=[],
        response_format=None,
        state={"messages": messages},
        runtime=MagicMock(),
        model_settings={},
    )


def _model_meta(multimodal: bool) -> MagicMock:
    meta = MagicMock()
    meta.supports_multimodal.return_value = multimodal
    return meta


def _make_corpus(tmp_path: Path) -> None:
    root = tmp_path / "tea" / "中国茶经" / "images"
    root.mkdir(parents=True)
    (root / "page0415_028.jpg").write_bytes(b"fake-image-bytes")


def _patched_env(tmp_path: Path, multimodal: bool):
    """base 数据根与模型能力注册表的统一 patch 上下文."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "src.tools.external.kb_read_image_tool.get_base_data_path",
            return_value=tmp_path,
        )
    )
    stack.enter_context(
        patch(
            "src.inference.llm.definitions.model_registry.get_model",
            return_value=_model_meta(multimodal),
        )
    )
    return stack


class TestKbImageInjectMiddleware:
    @pytest.mark.asyncio
    async def test_non_multimodal_passes_through(self, tmp_path):
        handler = AsyncMock(return_value=MagicMock())
        request = _make_request(
            "kb_read_image", {"image_ref": "tea:中国茶经/images/page0415_028.jpg"}
        )

        with _patched_env(tmp_path, multimodal=False):
            mw = KbImageInjectMiddleware("text-model")
            await mw.awrap_tool_call(request, handler)

        handler.assert_awaited_once_with(request)

    @pytest.mark.asyncio
    async def test_multimodal_kb_read_image_injects_original_image(self, tmp_path):
        _make_corpus(tmp_path)
        handler = AsyncMock(return_value=MagicMock())
        request = _make_request(
            "kb_read_image",
            {
                "image_ref": "tea:中国茶经/images/page0415_028.jpg",
                "prompt": "识别结构",
            },
        )

        with _patched_env(tmp_path, multimodal=True):
            mw = KbImageInjectMiddleware("vision-model")
            result = await mw.awrap_tool_call(request, handler)

        handler.assert_not_awaited()
        assert isinstance(result, Command)
        messages = result.update["messages"]
        assert isinstance(messages[0], ToolMessage)
        assert messages[0].name == "kb_read_image"
        human = messages[1]
        assert isinstance(human, HumanMessage)
        blocks = human.content
        image_blocks = [b for b in blocks if b.get("type") == "image_url"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    @pytest.mark.asyncio
    async def test_multimodal_other_tool_passes_through(self, tmp_path):
        handler = AsyncMock(return_value=MagicMock())
        request = _make_request("analyze_image", {"prompt": "x"})

        with _patched_env(tmp_path, multimodal=True):
            mw = KbImageInjectMiddleware("vision-model")
            await mw.awrap_tool_call(request, handler)

        handler.assert_awaited_once_with(request)

    @pytest.mark.asyncio
    async def test_invalid_ref_falls_back_to_handler(self, tmp_path):
        _make_corpus(tmp_path)
        handler = AsyncMock(return_value=MagicMock())
        request = _make_request(
            "kb_read_image", {"image_ref": "tea:../escape.jpg", "prompt": "x"}
        )

        with _patched_env(tmp_path, multimodal=True):
            mw = KbImageInjectMiddleware("vision-model")
            await mw.awrap_tool_call(request, handler)

        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_image_falls_back_to_handler(self, tmp_path):
        _make_corpus(tmp_path)
        handler = AsyncMock(return_value=MagicMock())
        request = _make_request(
            "kb_read_image", {"image_ref": "tea:中国茶经/images/none.jpg"}
        )

        with _patched_env(tmp_path, multimodal=True):
            mw = KbImageInjectMiddleware("vision-model")
            await mw.awrap_tool_call(request, handler)

        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_oversized_image_falls_back_to_handler(self, tmp_path):
        root = tmp_path / "tea" / "中国茶经" / "images"
        root.mkdir(parents=True)
        (root / "huge.jpg").write_bytes(b"x" * (6 * 1024 * 1024))
        handler = AsyncMock(return_value=MagicMock())
        request = _make_request(
            "kb_read_image", {"image_ref": "tea:中国茶经/images/huge.jpg"}
        )

        with _patched_env(tmp_path, multimodal=True):
            mw = KbImageInjectMiddleware("vision-model")
            await mw.awrap_tool_call(request, handler)

        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_parallel_kb_read_image_keeps_tool_messages_contiguous(
        self, tmp_path
    ):
        """并行 kb_read_image 直注后, 发往模型的消息须保持 tool_calls 响应连续.

        复现线上 400: 两次直注的 HumanMessage 按 ToolNode 执行序穿插在
        ToolMessage 之间, DeepSeek 拒绝该序列; awrap_model_call 须把直注
        HumanMessage 移到全部 ToolMessage 之后.
        """
        _make_corpus(tmp_path)
        root = tmp_path / "tea" / "中国茶经" / "images"
        (root / "page0415_029.jpg").write_bytes(b"fake-image-bytes-2")
        refs = [
            "tea:中国茶经/images/page0415_028.jpg",
            "tea:中国茶经/images/page0415_029.jpg",
        ]

        with _patched_env(tmp_path, multimodal=True):
            mw = KbImageInjectMiddleware("vision-model")

            # 模拟 ToolNode 顺序执行同一 AIMessage 的两个并行 tool_call,
            # LangGraph 按 Command 返回序追加合并 update
            updates: list[Any] = []
            for i, ref in enumerate(refs):
                req = ToolCallRequest(
                    tool_call={
                        "name": "kb_read_image",
                        "args": {"image_ref": ref, "prompt": "看图"},
                        "id": f"tc_{i}",
                    },
                    tool=MagicMock(),
                    state={},
                    runtime=MagicMock(),
                )
                cmd = await mw.awrap_tool_call(req, AsyncMock())
                assert isinstance(cmd, Command)
                updates.extend(cmd.update["messages"])

            ai_msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "kb_read_image",
                        "args": {"image_ref": ref},
                        "id": f"tc_{i}",
                    }
                    for i, ref in enumerate(refs)
                ],
            )
            # 穿插序: TM_A, HM_A(图), TM_B, HM_B(图) —— 线上 400 的消息形态
            messages = [HumanMessage(content="找图"), ai_msg, *updates]

            captured: dict[str, list[Any]] = {}

            async def handler(req: ModelRequest) -> AIMessage:
                captured["messages"] = list(req.messages)
                return AIMessage(content="ok")

            await mw.awrap_model_call(_make_model_request(messages), handler)

        sent = captured["messages"]
        assert sent[0] is messages[0]
        assert sent[1] is ai_msg
        # AIMessage 之后必须紧跟全部 ToolMessage (响应连续), 再排直注 HumanMessage
        assert {sent[2].tool_call_id, sent[3].tool_call_id} == {"tc_0", "tc_1"}
        assert all(isinstance(m, ToolMessage) for m in sent[2:4])
        assert all(isinstance(m, HumanMessage) for m in sent[4:6])
        image_blocks = [
            b for m in sent[4:6] for b in m.content if b.get("type") == "image_url"
        ]
        assert len(image_blocks) == 2

    @pytest.mark.asyncio
    async def test_single_kb_read_image_order_unchanged(self, tmp_path):
        """单次直注的合法序列 (AIM → TM → HM) 不被重排."""
        _make_corpus(tmp_path)
        ref = "tea:中国茶经/images/page0415_028.jpg"

        with _patched_env(tmp_path, multimodal=True):
            mw = KbImageInjectMiddleware("vision-model")
            req = ToolCallRequest(
                tool_call={
                    "name": "kb_read_image",
                    "args": {"image_ref": ref, "prompt": "看图"},
                    "id": "tc_0",
                },
                tool=MagicMock(),
                state={},
                runtime=MagicMock(),
            )
            cmd = await mw.awrap_tool_call(req, AsyncMock())
            ai_msg = AIMessage(
                content="",
                tool_calls=[
                    {"name": "kb_read_image", "args": {"image_ref": ref}, "id": "tc_0"}
                ],
            )
            messages = [HumanMessage(content="找图"), ai_msg, *cmd.update["messages"]]
            assert [type(m) for m in messages[1:]] == [
                type(ai_msg),
                ToolMessage,
                HumanMessage,
            ]

            captured: dict[str, Any] = {}

            async def handler(req: ModelRequest) -> AIMessage:
                captured["req"] = req
                return AIMessage(content="ok")

            await mw.awrap_model_call(_make_model_request(messages), handler)

        assert captured["req"].messages is messages

    @pytest.mark.asyncio
    async def test_non_multimodal_model_call_passes_through(self, tmp_path):
        """非多模态模型: 模型调用钩子原样透传 (无直注则无重排职责)."""
        interleaved = [
            HumanMessage(content="找图"),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "kb_read_image", "args": {}, "id": "tc_0"},
                    {"name": "kb_read_image", "args": {}, "id": "tc_1"},
                ],
            ),
            ToolMessage(content="a", tool_call_id="tc_0"),
            HumanMessage(content=[{"type": "text", "text": "图"}]),
            ToolMessage(content="b", tool_call_id="tc_1"),
        ]

        with _patched_env(tmp_path, multimodal=False):
            mw = KbImageInjectMiddleware("text-model")

            captured: dict[str, Any] = {}

            async def handler(req: ModelRequest) -> AIMessage:
                captured["req"] = req
                return AIMessage(content="ok")

            request = _make_model_request(interleaved)
            await mw.awrap_model_call(request, handler)

        assert captured["req"] is request
