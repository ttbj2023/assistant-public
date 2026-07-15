"""微信附件标记替换测试.

验证 [file: id] 标记经 ASCII 占位符隔离后, 在 markdown->html 转换中保持稳定,
确保标记后跟 markdown 语法 (加粗/代码/链接) 时不会导致 html 阶段字符串失配.
回归 bug: full_marker 含贪婪尾巴抓取的行尾说明文本, 经 markdown 转换后
字符串结构改变, html.replace 静默 no-op, 占位符原文泄漏进草稿箱.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.internal.wechat_publish.converter import md_to_wechat_html
from src.tools.internal.wechat_publish.service import (
    _replace_attachment_markers,
    _resolve_attachment_markers,
)

_ATTACH_SERVICE_PATH = (
    "src.storage.service.file_registry_service.create_file_registry_service"
)


def _make_db_entry(file_id: str, file_type: str = "image") -> MagicMock:
    """创建 mock DB 附件条目."""
    entry = MagicMock()
    entry.file_type = file_type
    entry.physical_path = f"files/exports/charts/{file_id}.png"
    return entry


def _patch_deps(
    db_entries: dict[str, MagicMock],
    resolved_path: Path,
) -> ExitStack:
    """patch _resolve_attachment_markers 的 DB 查询 + 路径解析 + 上下文."""
    mock_service = MagicMock()
    mock_service.get = AsyncMock(side_effect=lambda fid: db_entries.get(fid))

    mock_ctx = MagicMock()
    mock_ctx.agent_id = "test-agent"

    stack = ExitStack()
    stack.enter_context(
        patch("src.core.context.get_user_context", return_value=mock_ctx)
    )
    stack.enter_context(
        patch(_ATTACH_SERVICE_PATH, new=AsyncMock(return_value=mock_service))
    )
    stack.enter_context(
        patch(
            "src.core.path_resolver.resolve_attachment_internal_path",
            return_value=resolved_path,
        )
    )
    return stack


class TestResolveAttachmentMarkers:
    """_resolve_attachment_markers 占位符隔离测试."""

    @pytest.mark.asyncio
    async def test_marker_followed_by_bold_replaced(self, tmp_path: Path) -> None:
        """标记后跟 **加粗** 说明文本, 应替换为占位符而非保留 full_marker."""
        img = tmp_path / "a1b2c3d4.png"
        img.write_bytes(b"fake")
        client = MagicMock()
        client.upload_media = AsyncMock(
            return_value={"media_id": "m1", "url": "http://cdn/a1.png"}
        )

        content = "[file: a1b2c3d4] **饼图展示**"
        attachment_map: dict[str, str] = {}

        with _patch_deps({"a1b2c3d4": _make_db_entry("a1b2c3d4")}, img):
            result = await _resolve_attachment_markers(
                content, client, "user", "thread", attachment_map
            )

        assert "{WXATT:a1b2c3d4}" in result
        assert "[file: a1b2c3d4]" not in result
        assert "**饼图展示**" in result
        assert attachment_map["{WXATT:a1b2c3d4}"] == "http://cdn/a1.png"

    @pytest.mark.asyncio
    async def test_marker_followed_by_inline_code_replaced(
        self, tmp_path: Path
    ) -> None:
        """标记后跟 `代码` 说明文本, 同样隔离."""
        img = tmp_path / "b2c3d4e5.png"
        img.write_bytes(b"fake")
        client = MagicMock()
        client.upload_media = AsyncMock(
            return_value={"media_id": "m2", "url": "http://cdn/b2.png"}
        )

        content = "见 [file: b2c3d4e5] `MAU 数据` 对比"
        attachment_map: dict[str, str] = {}

        with _patch_deps({"b2c3d4e5": _make_db_entry("b2c3d4e5")}, img):
            result = await _resolve_attachment_markers(
                content, client, "user", "thread", attachment_map
            )

        assert "{WXATT:b2c3d4e5}" in result
        assert "[file: b2c3d4e5]" not in result
        assert "`MAU 数据`" in result

    @pytest.mark.asyncio
    async def test_two_markers_different_paragraphs(self, tmp_path: Path) -> None:
        """两个附件分处不同段落, 都应替换为各自占位符."""
        img1 = tmp_path / "a1b2c3d4.png"
        img1.write_bytes(b"fake1")
        img2 = tmp_path / "e5f6a7b8.png"
        img2.write_bytes(b"fake2")
        entries = {
            "a1b2c3d4": _make_db_entry("a1b2c3d4"),
            "e5f6a7b8": _make_db_entry("e5f6a7b8"),
        }
        client = MagicMock()
        client.upload_media = AsyncMock(
            side_effect=[
                {"media_id": "m1", "url": "http://cdn/a1.png"},
                {"media_id": "m2", "url": "http://cdn/e5.png"},
            ]
        )

        content = (
            "第一章:\n\n[file: a1b2c3d4] 饼图\n\n"
            "中间段落\n\n第二章:\n\n[file: e5f6a7b8] 柱状图"
        )
        attachment_map: dict[str, str] = {}

        mock_service = MagicMock()
        mock_service.get = AsyncMock(side_effect=lambda fid: entries.get(fid))
        mock_ctx = MagicMock()
        mock_ctx.agent_id = "test-agent"

        def resolve_side_effect(internal_path, user_id, thread_id):
            return img1 if "a1b2c3d4" in internal_path else img2

        with (
            patch("src.core.context.get_user_context", return_value=mock_ctx),
            patch(_ATTACH_SERVICE_PATH, new=AsyncMock(return_value=mock_service)),
            patch(
                "src.core.path_resolver.resolve_attachment_internal_path",
                side_effect=resolve_side_effect,
            ),
        ):
            result = await _resolve_attachment_markers(
                content, client, "user", "thread", attachment_map
            )

        assert "{WXATT:a1b2c3d4}" in result
        assert "{WXATT:e5f6a7b8}" in result
        assert "[file:" not in result
        assert len(attachment_map) == 2

    @pytest.mark.asyncio
    async def test_same_file_id_multiple_occurrences(self, tmp_path: Path) -> None:
        """同一 file_id 在文中出现多次, 都替换为同一占位符."""
        img = tmp_path / "a1b2c3d4.png"
        img.write_bytes(b"fake")
        client = MagicMock()
        client.upload_media = AsyncMock(
            return_value={"media_id": "m1", "url": "http://cdn/a1.png"}
        )

        content = "前文 [file: a1b2c3d4] 中\n\n后文 [file: a1b2c3d4] 再现"
        attachment_map: dict[str, str] = {}

        with _patch_deps({"a1b2c3d4": _make_db_entry("a1b2c3d4")}, img):
            result = await _resolve_attachment_markers(
                content, client, "user", "thread", attachment_map
            )

        assert result.count("{WXATT:a1b2c3d4}") == 2
        assert "[file: a1b2c3d4]" not in result
        assert len(attachment_map) == 1

    @pytest.mark.asyncio
    async def test_marker_without_space_matched(self, tmp_path: Path) -> None:
        """[file:id] 无空格写法应被兼容."""
        img = tmp_path / "a1b2c3d4.png"
        img.write_bytes(b"fake")
        client = MagicMock()
        client.upload_media = AsyncMock(
            return_value={"media_id": "m1", "url": "http://cdn/a1.png"}
        )

        content = "[file:a1b2c3d4] 无空格写法"
        attachment_map: dict[str, str] = {}

        with _patch_deps({"a1b2c3d4": _make_db_entry("a1b2c3d4")}, img):
            result = await _resolve_attachment_markers(
                content, client, "user", "thread", attachment_map
            )

        assert "{WXATT:a1b2c3d4}" in result
        assert "[file:" not in result

    @pytest.mark.asyncio
    async def test_non_image_attachment_removed(self, tmp_path: Path) -> None:
        """非图片附件 (document) 标记应被移除."""
        doc_entry = MagicMock()
        doc_entry.file_type = "document"
        doc_entry.physical_path = "files/exports/report.pdf"
        client = MagicMock()

        content = "参考 [file: a1b2c3d4] 报告"
        attachment_map: dict[str, str] = {}

        with _patch_deps({"a1b2c3d4": doc_entry}, tmp_path / "x"):
            result = await _resolve_attachment_markers(
                content, client, "user", "thread", attachment_map
            )

        assert "[file:" not in result
        assert len(attachment_map) == 0
        client.upload_media.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_file_id_removed(self, tmp_path: Path) -> None:
        """file_id 不在 DB 中, 标记应被移除."""
        client = MagicMock()

        content = "看 [file: deadbeef] 这张图"
        attachment_map: dict[str, str] = {}

        with _patch_deps({}, tmp_path / "x"):
            result = await _resolve_attachment_markers(
                content, client, "user", "thread", attachment_map
            )

        assert "[file:" not in result
        assert len(attachment_map) == 0

    @pytest.mark.asyncio
    async def test_upload_failure_removed(self, tmp_path: Path) -> None:
        """upload_media 抛异常, 标记应被移除."""
        img = tmp_path / "a1b2c3d4.png"
        img.write_bytes(b"fake")
        client = MagicMock()
        client.upload_media = AsyncMock(side_effect=RuntimeError("微信限流"))

        content = "[file: a1b2c3d4] 图"
        attachment_map: dict[str, str] = {}

        with _patch_deps({"a1b2c3d4": _make_db_entry("a1b2c3d4")}, img):
            result = await _resolve_attachment_markers(
                content, client, "user", "thread", attachment_map
            )

        assert "[file:" not in result
        assert len(attachment_map) == 0

    @pytest.mark.asyncio
    async def test_upload_returns_no_url_removed(self, tmp_path: Path) -> None:
        """upload_media 返回无 url 字段, 标记应被移除."""
        img = tmp_path / "a1b2c3d4.png"
        img.write_bytes(b"fake")
        client = MagicMock()
        client.upload_media = AsyncMock(return_value={"media_id": "m1"})

        content = "[file: a1b2c3d4] 图"
        attachment_map: dict[str, str] = {}

        with _patch_deps({"a1b2c3d4": _make_db_entry("a1b2c3d4")}, img):
            result = await _resolve_attachment_markers(
                content, client, "user", "thread", attachment_map
            )

        assert "[file:" not in result
        assert len(attachment_map) == 0

    @pytest.mark.asyncio
    async def test_file_not_exists_removed(self, tmp_path: Path) -> None:
        """物理文件不存在, 标记应被移除."""
        missing = tmp_path / "nonexistent.png"
        client = MagicMock()

        content = "[file: a1b2c3d4] 图"
        attachment_map: dict[str, str] = {}

        with _patch_deps({"a1b2c3d4": _make_db_entry("a1b2c3d4")}, missing):
            result = await _resolve_attachment_markers(
                content, client, "user", "thread", attachment_map
            )

        assert "[file:" not in result
        client.upload_media.assert_not_called()


class TestReplaceAttachmentMarkers:
    """_replace_attachment_markers 占位符替换测试."""

    def test_placeholder_replaced_with_img(self) -> None:
        """占位符应被替换为含 CDN url 的 <img> 标签."""
        html = "<p>前文 {WXATT:a1b2c3d4} 后文</p>"
        attachment_map = {"{WXATT:a1b2c3d4}": "http://cdn/a1.png"}

        result = _replace_attachment_markers(html, attachment_map)

        assert "<img" in result
        assert "http://cdn/a1.png" in result
        assert "{WXATT:a1b2c3d4}" not in result
        assert "前文" in result
        assert "后文" in result

    def test_empty_map_unchanged(self) -> None:
        """空 attachment_map, html 应原样返回."""
        html = "<p>无附件内容</p>"
        result = _replace_attachment_markers(html, {})
        assert result == html

    def test_residual_marker_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """html 中残留 [file: id] 字面量应触发 warning."""
        html = "<p>[file: deadbeef] 未处理</p>"

        with caplog.at_level("WARNING"):
            _replace_attachment_markers(html, {})

        assert any("残留未替换的附件标记" in r.message for r in caplog.records)

    def test_residual_placeholder_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """html 中残留 {WXATT:id} 占位符 (attachment_map 缺失对应key) 应触发 warning."""
        html = "<p>{WXATT:deadbeef} 未替换</p>"

        with caplog.at_level("WARNING"):
            _replace_attachment_markers(html, {})

        assert any("附件占位符" in r.message for r in caplog.records)

    def test_no_warning_when_clean(self, caplog: pytest.LogCaptureFixture) -> None:
        """无残留标记时不应触发 warning."""
        html = "<p>正常内容 {WXATT:a1b2c3d4}</p>"

        with caplog.at_level("WARNING"):
            _replace_attachment_markers(
                html, {"{WXATT:a1b2c3d4}": "http://cdn/a1.png"}
            )

        assert not any("残留" in r.message for r in caplog.records)


class TestEndToEndIsolation:
    """端到端验证: resolve -> 真实 md 转换 -> replace."""

    @pytest.mark.asyncio
    async def test_bold_marker_survives_markdown_transform(
        self, tmp_path: Path
    ) -> None:
        """标记后跟 **加粗**, 经真实 md 转换后占位符仍可精确替换为 <img>."""
        img = tmp_path / "a1b2c3d4.png"
        img.write_bytes(b"fake")
        client = MagicMock()
        client.upload_media = AsyncMock(
            return_value={"media_id": "m1", "url": "http://cdn/a1.png"}
        )

        raw_markdown = "[file: a1b2c3d4] **加粗说明**"
        attachment_map: dict[str, str] = {}

        with _patch_deps({"a1b2c3d4": _make_db_entry("a1b2c3d4")}, img):
            resolved = await _resolve_attachment_markers(
                raw_markdown, client, "user", "thread", attachment_map
            )

        final = _replace_attachment_markers(
            md_to_wechat_html(resolved), attachment_map
        )

        assert "<img" in final
        assert "http://cdn/a1.png" in final
        assert "加粗说明" in final
        assert "{WXATT:" not in final
        assert "[file:" not in final

    @pytest.mark.asyncio
    async def test_placeholder_survives_real_markdown_conversion(
        self, tmp_path: Path
    ) -> None:
        """占位符经真实 md_to_wechat_html 转换后仍可被精确替换为 <img>.

        回归: 旧占位符 __WXATT_{id}__ 的双下划线被 python-markdown 当作
        加粗语法, 转成 <strong>WXATT_{id}</strong>, 导致 replace 失配,
        图片 CDN URL 永不替换, 草稿箱显示蓝色加粗的 WXATT_xxx 而非图片.
        """
        img = tmp_path / "a1b2c3d4.png"
        img.write_bytes(b"fake")
        client = MagicMock()
        client.upload_media = AsyncMock(
            return_value={"media_id": "m1", "url": "http://cdn/a1.png"}
        )

        raw_markdown = "[file: a1b2c3d4] **加粗说明**"
        attachment_map: dict[str, str] = {}

        with _patch_deps({"a1b2c3d4": _make_db_entry("a1b2c3d4")}, img):
            resolved = await _resolve_attachment_markers(
                raw_markdown, client, "user", "thread", attachment_map
            )

        html = md_to_wechat_html(resolved)
        final = _replace_attachment_markers(html, attachment_map)

        assert "<img" in final
        assert "http://cdn/a1.png" in final
        assert "<strong>WXATT" not in final
        assert "WXATT_a1b2c3d4" not in final
        assert "[file:" not in final
