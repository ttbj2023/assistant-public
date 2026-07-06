"""WebFetchTool单元测试 - 验证HTTP网页抓取工具.

测试范围:
1. _fallback_extract - HTML降级提取逻辑
2. _execute - 结果格式化与错误处理
3. _arun - 缓存集成(仅缓存成功结果)
4. _check_anti_crawl - 反爬黑名单跳过逻辑
5. _fetch_content - HTTP请求与状态码处理
6. _extract_domain - 域名提取
7. trafilatura日志降噪 - 内部ERROR降级为WARNING

Mock策略: Mock httpx.AsyncClient避免真实HTTP请求, Mock ExpertCache避免全局缓存污染.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.tools.external.web_fetch import (
    _ANTI_CRAWL_DOMAINS,
    WebFetchTool,
    _extract_domain,
    _install_trafilatura_noise_filter,
    _TrafilaturaLevelFilter,
)

# =============================================================================
# 1. _fallback_extract 降级提取测试
# =============================================================================


class TestWebFetchToolFallbackExtract:
    """测试_fallback_extract静态方法"""

    def test_should_extract_text_from_simple_html(self):
        html = "<html><body><p>Hello World And Some More Text To Exceed Fifty Characters Threshold</p></body></html>"
        result = WebFetchTool._fallback_extract(html)
        assert result is not None
        assert "Hello World" in result

    def test_should_strip_script_tags(self):
        html = "<html><body><script>alert('xss')</script><p>Content That Is Long Enough To Pass The Minimum Length Check</p></body></html>"
        result = WebFetchTool._fallback_extract(html)
        assert result is not None
        assert "alert" not in result
        assert "Content" in result

    def test_should_strip_style_tags(self):
        html = "<html><body><style>.cls{color:red;}</style><p>Content That Is Long Enough To Pass The Minimum Length Check</p></body></html>"
        result = WebFetchTool._fallback_extract(html)
        assert result is not None
        assert ".cls" not in result
        assert "Content" in result

    def test_should_strip_nav_footer_header(self):
        html = (
            "<html><body>"
            "<nav>Navigation Links Here</nav>"
            "<header>Header Section Content Here</header>"
            "<main>Main Content Here That Is Long Enough To Pass The Minimum Threshold Check</main>"
            "<footer>Footer Links And Copyright Info</footer>"
            "</body></html>"
        )
        result = WebFetchTool._fallback_extract(html)
        assert result is not None
        assert "Main Content" in result
        assert "Navigation" not in result
        assert "Footer" not in result

    def test_should_return_none_for_short_content(self):
        html = "<html><body><p>Hi</p></body></html>"
        result = WebFetchTool._fallback_extract(html)
        assert result is None

    def test_should_collapse_whitespace(self):
        html = "<html><body><p>Word1   Word2   Word3   Word4   Word5   Word6   Word7   Word8   Word9   Word10   Word11</p></body></html>"
        result = WebFetchTool._fallback_extract(html)
        assert result is not None
        assert "  " not in result

    def test_should_return_none_on_invalid_html(self):
        result = WebFetchTool._fallback_extract(None)
        assert result is None


# =============================================================================
# 2. _execute 结果格式化与错误处理测试
# =============================================================================


class TestWebFetchToolExecute:
    """测试_execute方法的输出格式"""

    @pytest.mark.asyncio
    async def test_should_return_success_format_when_content_available(self):
        tool = WebFetchTool()
        with patch.object(tool, "_fetch_content", return_value="Some content here"):
            result_str = await tool._execute("https://example.com")
            result = json.loads(result_str)
            assert result["status"] == "success"
            assert result["url"] == "https://example.com"
            assert result["content"] == "Some content here"
            assert result["word_count"] > 0
            assert result["source"] == "fetch_webpage"

    @pytest.mark.asyncio
    async def test_should_return_failed_format_when_content_is_none(self):
        tool = WebFetchTool()
        with patch.object(tool, "_fetch_content", return_value=None):
            result_str = await tool._execute("https://example.com")
            result = json.loads(result_str)
            assert result["status"] == "failed"
            assert result["url"] == "https://example.com"
            assert result["content"] == ""

    @pytest.mark.asyncio
    async def test_should_truncate_long_content(self):
        tool = WebFetchTool(max_content_length=100)
        long_content = "word " * 2000
        with patch.object(tool, "_fetch_content", return_value=long_content):
            result_str = await tool._execute("https://example.com")
            result = json.loads(result_str)
            assert len(result["content"]) <= 100

    @pytest.mark.asyncio
    async def test_should_handle_timeout_without_exception_traceback(self):
        tool = WebFetchTool()
        with patch.object(
            tool,
            "_fetch_content",
            side_effect=httpx.TimeoutException("read timeout"),
        ):
            result_str = await tool._execute("https://example.com")
            result = json.loads(result_str)
            assert result["status"] == "failed"
            assert "超时" in result["error"]

    @pytest.mark.asyncio
    async def test_should_handle_http_status_error(self):
        tool = WebFetchTool()
        mock_response = MagicMock()
        mock_response.status_code = 404
        with patch.object(
            tool,
            "_fetch_content",
            side_effect=httpx.HTTPStatusError(
                "Not Found", request=MagicMock(), response=mock_response
            ),
        ):
            result_str = await tool._execute("https://example.com")
            result = json.loads(result_str)
            assert result["status"] == "failed"
            assert "HTTP 404" in result["error"]

    @pytest.mark.asyncio
    async def test_should_handle_connect_error(self):
        tool = WebFetchTool()
        with patch.object(
            tool,
            "_fetch_content",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            result_str = await tool._execute("https://example.com")
            result = json.loads(result_str)
            assert result["status"] == "failed"
            assert "连接失败" in result["error"]

    @pytest.mark.asyncio
    async def test_should_handle_unexpected_exception(self):
        tool = WebFetchTool()
        with patch.object(
            tool, "_fetch_content", side_effect=RuntimeError("unexpected")
        ):
            result_str = await tool._execute("https://example.com")
            result = json.loads(result_str)
            assert result["status"] == "failed"
            assert "unexpected" in result["error"]


# =============================================================================
# 3. _arun 缓存集成测试
# =============================================================================


class TestWebFetchToolArun:
    """测试_arun方法的缓存集成"""

    @pytest.mark.asyncio
    async def test_should_return_cached_result_when_available(self):
        tool = WebFetchTool()
        cached_result = json.dumps({
            "url": "https://example.com",
            "content": "cached",
            "status": "success",
        })

        mock_cache = AsyncMock()
        mock_cache.get_fetch = AsyncMock(return_value=cached_result)
        mock_cache.set_fetch = AsyncMock()

        with patch(
            "src.tools.external.web_fetch.get_expert_cache",
            return_value=mock_cache,
        ):
            result = await tool._arun("https://example.com")
            assert result == cached_result
            mock_cache.set_fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_cache_success_result_after_fetch(self):
        tool = WebFetchTool()
        fetch_result = json.dumps({
            "url": "https://example.com",
            "content": "fresh",
            "status": "success",
        })

        mock_cache = AsyncMock()
        mock_cache.get_fetch = AsyncMock(return_value=None)
        mock_cache.set_fetch = AsyncMock()

        with (
            patch(
                "src.tools.external.web_fetch.get_expert_cache",
                return_value=mock_cache,
            ),
            patch.object(tool, "_execute", return_value=fetch_result),
        ):
            result = await tool._arun("https://example.com")
            assert result == fetch_result
            mock_cache.set_fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_not_cache_failed_result(self):
        tool = WebFetchTool()
        fetch_result = json.dumps({
            "url": "https://example.com",
            "content": "",
            "status": "failed",
            "error": "HTTP 404",
        })

        mock_cache = AsyncMock()
        mock_cache.get_fetch = AsyncMock(return_value=None)
        mock_cache.set_fetch = AsyncMock()

        with (
            patch(
                "src.tools.external.web_fetch.get_expert_cache",
                return_value=mock_cache,
            ),
            patch.object(tool, "_execute", return_value=fetch_result),
        ):
            result = await tool._arun("https://example.com")
            assert result == fetch_result
            mock_cache.set_fetch.assert_not_called()


# =============================================================================
# 4. _check_anti_crawl 反爬黑名单测试
# =============================================================================


class TestAntiCrawlCheck:
    """测试反爬域名检测与跳过逻辑"""

    # 从源码常量动态生成, 域名列表变更时测试自动跟进
    @pytest.mark.parametrize(
        "url",
        [f"https://{domain}/article/123" for domain in sorted(_ANTI_CRAWL_DOMAINS)],
        ids=lambda u: u.split("//")[1].split("/")[0],
    )
    def test_should_skip_anti_crawl_domains(self, url: str):
        result = WebFetchTool._check_anti_crawl(url)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["status"] == "skipped"
        assert "zhipu_reader" in parsed["error"]

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/article",
            "https://github.com/org/repo",
            "https://docs.python.org/3/library/",
            "https://huggingface.co/docs",
            "https://www.cnblogs.com/some/post.html",
            "https://juejin.cn/post/123",
            "https://www.jianshu.com/p/abc",
        ],
    )
    def test_should_not_skip_normal_domains(self, url: str):
        result = WebFetchTool._check_anti_crawl(url)
        assert result is None

    def test_should_handle_invalid_url(self):
        result = WebFetchTool._check_anti_crawl("not-a-url")
        assert result is None

    @pytest.mark.asyncio
    async def test_arun_should_skip_anti_crawl_without_http_request(self):
        tool = WebFetchTool()
        mock_cache = AsyncMock()
        mock_cache.get_fetch = AsyncMock(return_value=None)
        mock_cache.set_fetch = AsyncMock()

        with (
            patch(
                "src.tools.external.web_fetch.get_expert_cache",
                return_value=mock_cache,
            ),
            patch.object(tool, "_execute") as mock_execute,
        ):
            result = await tool._arun("https://zhuanlan.zhihu.com/p/123")
            parsed = json.loads(result)
            assert parsed["status"] == "skipped"
            mock_execute.assert_not_called()
            mock_cache.set_fetch.assert_not_called()


# =============================================================================
# 5. _fetch_content HTTP请求与状态码处理测试
# =============================================================================


class TestFetchContentHttpStatusCodes:
    """测试_fetch_content对不同HTTP状态码的处理"""

    @pytest.mark.asyncio
    async def test_should_return_none_for_404(self):
        tool = WebFetchTool()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "<html>Not Found</html>"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await tool._fetch_content("https://example.com/missing")
            assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_for_403(self):
        tool = WebFetchTool()
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "<html>Forbidden</html>"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await tool._fetch_content("https://example.com/blocked")
            assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_for_500(self):
        tool = WebFetchTool()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "<html>Server Error</html>"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await tool._fetch_content("https://example.com/error")
            assert result is None

    @pytest.mark.asyncio
    async def test_should_extract_content_for_200(self):
        tool = WebFetchTool()
        html = (
            "<html><body><article>"
            + "Main article content with enough text to pass extraction threshold. " * 5
            + "</article></body></html>"
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await tool._fetch_content("https://example.com/article")
            assert result is not None
            assert len(result) > 0


# =============================================================================
# 6. _extract_domain 辅助函数测试
# =============================================================================


class TestExtractDomain:
    """测试URL域名提取"""

    def test_should_extract_simple_domain(self):
        assert _extract_domain("https://example.com/path") == "example.com"

    def test_should_extract_subdomain(self):
        assert _extract_domain("https://blog.example.com/path") == "blog.example.com"

    def test_should_handle_no_scheme(self):
        assert _extract_domain("example.com/path") == ""

    def test_should_handle_empty_string(self):
        assert _extract_domain("") == ""


# =============================================================================
# 7. trafilatura 内部日志降噪测试
# =============================================================================


class TestTrafilaturaLevelFilter:
    """测试 trafilatura 内部 ERROR 日志降级为 WARNING 的降噪逻辑.

    背景: trafilatura 遇到无法解析的 HTML (JS 外壳/空文档) 时, 经其自身 logger
    以 ERROR 级别记录日志, 属解析能力限制而非工具/网络错误, 本工具已优雅降级.
    """

    def test_should_downgrade_error_to_warning(self):
        record = logging.LogRecord(
            name="trafilatura.utils",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="parsed tree length: 1, wrong data type or not valid HTML",
            args=(),
            exc_info=None,
        )
        assert _TrafilaturaLevelFilter().filter(record) is True
        assert record.levelno == logging.WARNING
        assert record.levelname == "WARNING"

    def test_should_downgrade_critical_to_warning(self):
        record = logging.LogRecord(
            name="trafilatura.core",
            level=logging.CRITICAL,
            pathname="",
            lineno=0,
            msg="empty HTML tree: None",
            args=(),
            exc_info=None,
        )
        assert _TrafilaturaLevelFilter().filter(record) is True
        assert record.levelno == logging.WARNING

    def test_should_keep_warning_unchanged(self):
        record = logging.LogRecord(
            name="trafilatura.utils",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="minor issue",
            args=(),
            exc_info=None,
        )
        assert _TrafilaturaLevelFilter().filter(record) is True
        assert record.levelno == logging.WARNING

    def test_should_keep_info_unchanged(self):
        record = logging.LogRecord(
            name="trafilatura.utils",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="debug info",
            args=(),
            exc_info=None,
        )
        assert _TrafilaturaLevelFilter().filter(record) is True
        assert record.levelno == logging.INFO

    def test_install_should_attach_filter_to_trafilatura_loggers(self):
        _install_trafilatura_noise_filter()
        for name in ("trafilatura", "trafilatura.utils", "trafilatura.core"):
            noisy_logger = logging.getLogger(name)
            assert any(
                isinstance(f, _TrafilaturaLevelFilter) for f in noisy_logger.filters
            )

    def test_install_should_be_idempotent(self):
        _install_trafilatura_noise_filter()
        _install_trafilatura_noise_filter()
        noisy_logger = logging.getLogger("trafilatura.core")
        attached = [
            f for f in noisy_logger.filters if isinstance(f, _TrafilaturaLevelFilter)
        ]
        assert len(attached) == 1
