"""InferenceCoordinator 历史签名 URL 还原单元测试.

覆盖 _restore_file_markers_in_history:
- 命中有效 file_id 时把 markdown 签名链接替换为 [file: id] label
- 反查未命中 / service 不可用时保留原 URL
- 纯文本历史 / 已是标记的历史零改动
- list content 的 text 块 / 多 URL / 裸 URL 边界

Mock 策略: create_file_registry_service 用 patch (自动 AsyncMock),
service.get 用 side_effect 按 file_id 返回命中/未命中.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agent.processors.inference_coordinator import InferenceCoordinator

_VALID_SIG = "a1b2c3d4e5f6a7b8a1b2c3d4e5f6a7b8"  # 32 hex


@pytest.fixture
def coordinator() -> InferenceCoordinator:
    return InferenceCoordinator({"llm": {"model": "test"}})


def _make_signed_url(
    file_id: str = "abc12345",
    filename: str = "chart.png",
    expiry: int = 1751248000,
) -> str:
    """构造本服务签名下载 URL (路径含明文 file_id)."""
    return (
        f"http://localhost:8000/v1/files/dl/u1/t1/a1/"
        f"{file_id}/{expiry}/{_VALID_SIG}/{filename}"
    )


def _mock_registry_service(valid_fids: set[str]) -> AsyncMock:
    """构造 file_registry_service mock, get 按 fid 返回命中/未命中."""

    async def _get(fid: str) -> object:
        return MagicMock(file_id=fid) if fid in valid_fids else None

    svc = AsyncMock()
    svc.get = AsyncMock(side_effect=_get)
    return svc


def _patch_service(mock_service: AsyncMock | None):
    """patch create_file_registry_service 返回指定 service."""
    return patch(
        "src.storage.service.file_registry_service.create_file_registry_service",
        return_value=mock_service,
    )


# ========== _restore_file_markers_in_history ==========


class TestRestoreFileMarkers:
    async def test_replaces_valid_signed_url(self, coordinator):
        """命中有效 file_id, markdown 图片链接替换为 [file: id] label."""
        url = _make_signed_url("abc12345", "chart.png")
        msg = AIMessage(content=f"图表已生成\n---\n![chart.png]({url})")
        with _patch_service(_mock_registry_service({"abc12345"})):
            result = await coordinator._restore_file_markers_in_history([msg], "u1")
        assert result is not None
        assert "[file: abc12345] chart.png" in result[0].content
        assert url not in result[0].content

    async def test_replaces_doc_download_link(self, coordinator):
        """非图片的下载链接 (无 ! 前缀) 同样替换."""
        url = _make_signed_url("deadbeef", "report.pdf")
        msg = AIMessage(content=f"文档已导出\n[report.pdf]({url})")
        with _patch_service(_mock_registry_service({"deadbeef"})):
            result = await coordinator._restore_file_markers_in_history([msg], "u1")
        assert result is not None
        assert "[file: deadbeef] report.pdf" in result[0].content

    async def test_keeps_url_when_file_not_found(self, coordinator):
        """反查未命中 (文件已被配额清理), 保留原 URL 不替换."""
        url = _make_signed_url("abc12345", "chart.png")
        msg = AIMessage(content=f"![chart.png]({url})")
        with _patch_service(_mock_registry_service(set())):
            result = await coordinator._restore_file_markers_in_history([msg], "u1")
        assert result is not None
        assert url in result[0].content
        assert "[file:" not in result[0].content

    async def test_keeps_url_when_service_unavailable(self, coordinator):
        """service 创建失败 (None), 保留原样."""
        url = _make_signed_url("abc12345", "chart.png")
        msg = AIMessage(content=f"![chart.png]({url})")
        with _patch_service(None):
            result = await coordinator._restore_file_markers_in_history([msg], "u1")
        assert result is not None
        assert url in result[0].content

    async def test_plain_text_history_unchanged(self, coordinator):
        """纯文本历史无签名 URL, 原样返回 (且不查表)."""
        msg = HumanMessage(content="普通对话历史, 没有文件链接")
        result = await coordinator._restore_file_markers_in_history([msg], "u1")
        assert result is not None
        assert result[0].content == "普通对话历史, 没有文件链接"

    async def test_already_marker_history_unchanged(self, coordinator):
        """已是 [file: id] 标记的历史 (local 模式) 原样返回."""
        msg = AIMessage(content="[file: abc12345] PNG导出: chart.png")
        result = await coordinator._restore_file_markers_in_history([msg], "u1")
        assert result is not None
        assert result[0].content == "[file: abc12345] PNG导出: chart.png"

    async def test_none_history_returns_none(self, coordinator):
        result = await coordinator._restore_file_markers_in_history(None, "u1")
        assert result is None

    async def test_empty_history_returns_empty(self, coordinator):
        result = await coordinator._restore_file_markers_in_history([], "u1")
        assert result == []

    async def test_replaces_url_in_list_text_block(self, coordinator):
        """list content 内 text 块里的 URL 同样替换."""
        url = _make_signed_url("abc12345", "chart.png")
        msg = HumanMessage(
            content=[
                {"type": "text", "text": "上一轮结果"},
                {"type": "text", "text": f"![chart.png]({url})"},
            ]
        )
        with _patch_service(_mock_registry_service({"abc12345"})):
            result = await coordinator._restore_file_markers_in_history([msg], "u1")
        assert result is not None
        texts = [b["text"] for b in result[0].content if b.get("type") == "text"]
        assert any("[file: abc12345] chart.png" in t for t in texts)
        assert texts[0] == "上一轮结果"  # 其他块保留

    async def test_preserves_non_text_blocks(self, coordinator):
        """list content 内非 text 块 (如 image_url) 保留不变."""
        url = _make_signed_url("abc12345", "chart.png")
        msg = HumanMessage(
            content=[
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,xxx"},
                },
                {"type": "text", "text": f"![chart.png]({url})"},
            ]
        )
        with _patch_service(_mock_registry_service({"abc12345"})):
            result = await coordinator._restore_file_markers_in_history([msg], "u1")
        assert result is not None
        assert result[0].content[0]["type"] == "image_url"

    async def test_multiple_urls_partial_hit(self, coordinator):
        """多个 URL, 部分命中部分未命中, 只替换命中的."""
        url_hit = _make_signed_url("abc12345", "chart.png")
        url_miss = _make_signed_url("00000000", "old.png")
        msg = AIMessage(content=f"![chart.png]({url_hit})\n![old.png]({url_miss})")
        with _patch_service(_mock_registry_service({"abc12345"})):
            result = await coordinator._restore_file_markers_in_history([msg], "u1")
        assert result is not None
        assert "[file: abc12345] chart.png" in result[0].content
        assert url_miss in result[0].content  # 未命中保留原 URL

    async def test_bare_url_not_replaced(self, coordinator):
        """裸 URL (非 markdown 链接语法) 不匹配, 保留原样."""
        url = _make_signed_url("abc12345", "chart.png")
        msg = AIMessage(content=f"直接贴的链接: {url}")
        with _patch_service(_mock_registry_service({"abc12345"})):
            result = await coordinator._restore_file_markers_in_history([msg], "u1")
        assert result is not None
        assert result[0].content == f"直接贴的链接: {url}"

    async def test_non_service_url_not_replaced(self, coordinator):
        """非本服务结构的普通 markdown 链接不误伤."""
        msg = AIMessage(content="[Google](https://google.com/logo.png)")
        with _patch_service(_mock_registry_service({"abc12345"})):
            result = await coordinator._restore_file_markers_in_history([msg], "u1")
        assert result is not None
        assert result[0].content == "[Google](https://google.com/logo.png)"

    async def test_message_type_preserved(self, coordinator):
        """替换后消息类型 (AIMessage/HumanMessage) 保持不变."""
        url = _make_signed_url("abc12345", "chart.png")
        ai_msg = AIMessage(content=f"![chart.png]({url})")
        with _patch_service(_mock_registry_service({"abc12345"})):
            result = await coordinator._restore_file_markers_in_history([ai_msg], "u1")
        assert result is not None
        assert isinstance(result[0], AIMessage)
