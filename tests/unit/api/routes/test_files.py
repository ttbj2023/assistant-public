"""文件下载路由单元测试.

测试 src/api/routes/files.py 的 4xx 错误分支:
- 410 链接已过期
- 401 签名无效
- 404 附件不存在
- 404 物理文件已被清理

Mock 依赖: signed_url_provider, attachment_registry_service, path_resolver.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.files import download_file
from src.files.signed_url import SignedURLProvider

SECRET = "test-secret-key-for-files-route-32c"


def _make_provider() -> SignedURLProvider:
    return SignedURLProvider(secret=SECRET)


@pytest.fixture
def patched_provider():
    provider = _make_provider()
    with patch(
        "src.api.routes.files.get_signed_url_provider",
        return_value=provider,
    ):
        yield provider


@pytest.fixture
def file_on_disk(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"fake-pdf-content")
    return f


class TestDownloadFile:
    """download_file 路由函数."""

    @pytest.mark.asyncio
    async def test_expired_returns_410(self, patched_provider):
        expiry = int(time.time()) - 100
        sig = patched_provider.sign("u1", "t1", "a1", "f1", expiry)
        with pytest.raises(HTTPException) as exc:
            await download_file("u1", "t1", "a1", "f1", expiry, sig, "x.pdf")
        assert exc.value.status_code == 410
        assert "过期" in exc.value.detail

    @pytest.mark.asyncio
    async def test_invalid_signature_returns_401(self, patched_provider):
        expiry = int(time.time()) + 3600
        with pytest.raises(HTTPException) as exc:
            await download_file(
                "u1",
                "t1",
                "a1",
                "f1",
                expiry,
                "deadbeef" + "0" * 24,
                "x.pdf",
            )
        assert exc.value.status_code == 401
        assert "签名" in exc.value.detail

    @pytest.mark.asyncio
    async def test_attachment_not_found_returns_404(self, patched_provider):
        expiry = int(time.time()) + 3600
        sig = patched_provider.sign("u1", "t1", "a1", "f1", expiry)
        with (
            patch(
                "src.api.routes.files._lookup_attachment",
                new=AsyncMock(return_value=None),
            ),
            pytest.raises(HTTPException) as exc,
        ):
            await download_file("u1", "t1", "a1", "f1", expiry, sig, "x.pdf")
        assert exc.value.status_code == 404
        assert "不存在" in exc.value.detail or "清除" in exc.value.detail

    @pytest.mark.asyncio
    async def test_file_missing_on_disk_returns_404(
        self,
        patched_provider,
        tmp_path,
    ):
        expiry = int(time.time()) + 3600
        sig = patched_provider.sign("u1", "t1", "a1", "f1", expiry)

        mock_entry = MagicMock()
        mock_entry.filename = "gone.pdf"
        mock_entry.internal_path = "shared/files/exports/gone.pdf"

        missing_path = tmp_path / "gone.pdf"

        with (
            patch(
                "src.api.routes.files._lookup_attachment",
                new=AsyncMock(return_value=mock_entry),
            ),
            patch(
                "src.api.routes.files.resolve_attachment_internal_path",
                return_value=missing_path,
            ),
            pytest.raises(HTTPException) as exc,
        ):
            await download_file("u1", "t1", "a1", "f1", expiry, sig, "gone.pdf")
        assert exc.value.status_code == 404
        assert "清理" in exc.value.detail

    @pytest.mark.asyncio
    async def test_successful_download_returns_file_response(
        self,
        patched_provider,
        file_on_disk,
    ):
        expiry = int(time.time()) + 3600
        sig = patched_provider.sign("u1", "t1", "a1", "f1", expiry)

        mock_entry = MagicMock()
        mock_entry.filename = "doc.pdf"
        mock_entry.internal_path = "shared/files/exports/doc.pdf"

        with (
            patch(
                "src.api.routes.files._lookup_attachment",
                new=AsyncMock(return_value=mock_entry),
            ),
            patch(
                "src.api.routes.files.resolve_attachment_internal_path",
                return_value=file_on_disk,
            ),
        ):
            response = await download_file(
                "u1",
                "t1",
                "a1",
                "f1",
                expiry,
                sig,
                "doc.pdf",
            )

        from fastapi.responses import FileResponse as _FR

        assert isinstance(response, _FR)
        assert response.filename == "doc.pdf"

    @pytest.mark.asyncio
    async def test_zero_expiry_never_expires(self, patched_provider, file_on_disk):
        sig = patched_provider.sign("u1", "t1", "a1", "f1", 0)

        mock_entry = MagicMock()
        mock_entry.filename = "doc.pdf"
        mock_entry.internal_path = "shared/files/exports/doc.pdf"

        with (
            patch(
                "src.api.routes.files._lookup_attachment",
                new=AsyncMock(return_value=mock_entry),
            ),
            patch(
                "src.api.routes.files.resolve_attachment_internal_path",
                return_value=file_on_disk,
            ),
        ):
            response = await download_file("u1", "t1", "a1", "f1", 0, sig, "doc.pdf")

        from fastapi.responses import FileResponse as _FR

        assert isinstance(response, _FR)


class TestGuessMediaType:
    """MIME 类型推断."""

    def test_pdf(self):
        from src.api.routes.files import _guess_media_type

        assert _guess_media_type("doc.pdf") == "application/pdf"

    def test_png(self):
        from src.api.routes.files import _guess_media_type

        assert _guess_media_type("img.png") == "image/png"

    def test_unknown_returns_octet_stream(self):
        from src.api.routes.files import _guess_media_type

        assert _guess_media_type("file.xyz") == "application/octet-stream"

    def test_no_extension_returns_octet_stream(self):
        from src.api.routes.files import _guess_media_type

        assert _guess_media_type("README") == "application/octet-stream"
