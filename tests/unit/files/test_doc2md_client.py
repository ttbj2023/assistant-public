"""Doc2mdClient 单元测试.

Mock httpx 传输层, 验证请求构造 (鉴权/参数) 与响应解析/降级行为.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.files.doc2md_client import Doc2mdClient, Doc2mdError, Doc2mdResult


def _ok_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=payload)
    return resp


class TestIsConfigured:
    """服务配置检测."""

    def test_not_configured_without_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """base_url 未配置: is_configured=False."""
        monkeypatch.delenv("DOC2MD_BASE_URL", raising=False)
        client = Doc2mdClient()
        assert client.is_configured() is False

    def test_configured_with_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOC2MD_BASE_URL", "http://127.0.0.1:8769")
        client = Doc2mdClient()
        assert client.is_configured() is True


class TestConvert:
    """convert 调用链."""

    @pytest.mark.asyncio
    async def test_convert_sends_auth_and_parses_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """正常调用: Bearer 鉴权 + base64 载荷 + 响应解析为结果对象."""
        monkeypatch.setenv("DOC2MD_BASE_URL", "http://127.0.0.1:8769")
        monkeypatch.setenv("DOC2MD_TOKEN", "secret-token")

        client = Doc2mdClient()
        resp = _ok_response({
            "success": True,
            "markdown": "# 标题",
            "images": [{"name": "image_001.png", "content_b64": "aW1n"}],
            "mode": "digital-pdf",
            "pages": 3,
            "elapsed": 0.5,
        })

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=resp)

        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.convert("报告.pdf", b"\x89PNG")

        assert isinstance(result, Doc2mdResult)
        assert result.markdown == "# 标题"
        assert result.mode == "digital-pdf"
        assert result.pages == 3
        assert result.images[0]["name"] == "image_001.png"

        # 请求构造校验
        call = mock_http.post.call_args
        assert call.args[0] == "/convert"
        payload = call.kwargs["json"]
        assert payload["filename"] == "报告.pdf"
        assert payload["defer_scan"] is False
        import base64

        assert base64.b64decode(payload["content_b64"]) == b"\x89PNG"
        assert call.kwargs["headers"]["Authorization"] == "Bearer secret-token"

    @pytest.mark.asyncio
    async def test_convert_defer_scan_passthrough(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """defer_scan=True 透传, scan-deferred 响应正常解析."""
        monkeypatch.setenv("DOC2MD_BASE_URL", "http://127.0.0.1:8769")
        monkeypatch.setenv("DOC2MD_TOKEN", "t")

        client = Doc2mdClient()
        resp = _ok_response({
            "success": True,
            "markdown": "",
            "images": [],
            "mode": "scan-deferred",
            "pages": 12,
            "elapsed": 0.2,
        })

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=resp)

        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            result = await client.convert("扫描件.pdf", b"pdf", defer_scan=True)

        assert result.mode == "scan-deferred"
        assert result.pages == 12
        assert mock_http.post.call_args.kwargs["json"]["defer_scan"] is True

    @pytest.mark.asyncio
    async def test_convert_not_configured_returns_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """服务未配置: 返回 None (调用方降级为提示行)."""
        monkeypatch.delenv("DOC2MD_BASE_URL", raising=False)
        client = Doc2mdClient()

        result = await client.convert("a.pdf", b"x")
        assert result is None

    @pytest.mark.asyncio
    async def test_convert_http_error_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """服务 4xx/5xx: 抛 Doc2mdError (带状态码)."""
        monkeypatch.setenv("DOC2MD_BASE_URL", "http://127.0.0.1:8769")
        monkeypatch.setenv("DOC2MD_TOKEN", "t")

        client = Doc2mdClient()
        resp = MagicMock()
        resp.status_code = 422
        resp.text = '{"detail": "转换失败"}'
        status_error = httpx.HTTPStatusError(
            "server error",
            request=MagicMock(),
            response=resp,
        )
        resp.raise_for_status = MagicMock(side_effect=status_error)

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=resp)

        with (
            patch.object(client, "_get_client", AsyncMock(return_value=mock_http)),
            pytest.raises(Doc2mdError, match="422"),
        ):
            await client.convert("坏.pdf", b"x")
