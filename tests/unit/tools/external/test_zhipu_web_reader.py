"""ZhipuReaderTool 单元测试.

测试智谱AI网页阅读工具的核心逻辑, Mock外部API调用.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.tools.external.zhipu_web_reader import (
    ZhipuReaderTool,
    _check_url_reachable,
    _execute_reader,
    _extract_from_code_block,
    zhipu_web_reader,
)


@pytest.fixture
def reader_tool():
    """创建智谱网页阅读工具实例."""
    return ZhipuReaderTool()


class TestZhipuReaderToolAvailability:
    @pytest.mark.asyncio
    async def test_is_available_should_return_true_when_key_set(self):
        """API Key存在时应返回True."""
        tool = ZhipuReaderTool()
        with patch("src.tools.external.zhipu_web_reader._get_api_key", return_value="test_key"):
            result = await tool.is_available()
            assert result is True

    @pytest.mark.asyncio
    async def test_is_available_should_return_false_when_key_missing(self):
        """API Key不存在时应返回False."""
        tool = ZhipuReaderTool()
        with patch("src.tools.external.zhipu_web_reader._get_api_key", return_value=""):
            result = await tool.is_available()
            assert result is False


class TestZhipuReaderToolRun:
    @pytest.mark.asyncio
    async def test_arun_should_return_formatted_content(self, reader_tool):
        """成功读取应返回标题+内容格式."""
        mock_result = {
            "content": "这是网页正文内容",
            "title": "测试文章",
            "description": "文章描述",
            "url": "https://example.com",
        }

        with patch(
            "src.tools.external.zhipu_web_reader.zhipu_web_reader",
            return_value=mock_result,
        ):
            result = await reader_tool._arun(url="https://example.com")

        assert "测试文章" in result
        assert "这是网页正文内容" in result

    @pytest.mark.asyncio
    async def test_arun_should_return_error_json_on_failure(self, reader_tool):
        """API错误时应返回错误JSON."""
        error_result = {"error": "网页阅读失败: 连接超时"}

        with patch(
            "src.tools.external.zhipu_web_reader.zhipu_web_reader",
            return_value=error_result,
        ):
            result = await reader_tool._arun(url="https://bad-url.com")

        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_arun_should_handle_empty_title(self, reader_tool):
        """无标题时应只返回内容."""
        mock_result = {
            "content": "正文内容",
            "title": "",
            "url": "https://example.com",
        }

        with patch(
            "src.tools.external.zhipu_web_reader.zhipu_web_reader",
            return_value=mock_result,
        ):
            result = await reader_tool._arun(url="https://example.com")

        assert "标题:" not in result
        assert "正文内容" in result


class TestZhipuWebReaderFunction:
    @pytest.mark.asyncio
    async def test_should_return_error_when_no_api_key(self):
        """无API Key时应返回错误."""
        with patch("src.tools.external.zhipu_web_reader._get_api_key", return_value=""):
            result = await zhipu_web_reader("https://example.com")

        assert "error" in result
        assert "ZHIPU_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_should_return_cached_data(self):
        """缓存命中时直接返回缓存."""
        cached = json.dumps({
            "content": "缓存内容",
            "title": "缓存标题",
        })
        with patch("src.tools.external.zhipu_web_reader._get_api_key", return_value="key"):
            with patch(
                "src.tools.external.zhipu_web_reader.get_expert_cache",
            ) as mock_cache_fn:
                mock_cache = AsyncMock()
                mock_cache.get_fetch.return_value = cached
                mock_cache_fn.return_value = mock_cache

                result = await zhipu_web_reader("https://example.com")

        assert result["content"] == "缓存内容"

    @pytest.mark.asyncio
    async def test_should_cache_successful_result(self):
        """成功读取后应缓存结果."""
        success_result = {
            "content": "网页内容",
            "title": "文章标题",
        }
        with (
            patch("src.tools.external.zhipu_web_reader._get_api_key", return_value="key"),
            patch(
                "src.tools.external.zhipu_web_reader._execute_reader",
                return_value=success_result,
            ),
            patch(
                "src.tools.external.zhipu_web_reader.get_expert_cache",
            ) as mock_cache_fn,
        ):
            mock_cache = AsyncMock()
            mock_cache.get_fetch.return_value = None
            mock_cache_fn.return_value = mock_cache

            result = await zhipu_web_reader("https://example.com")

        assert result["content"] == "网页内容"
        mock_cache.set_fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_not_cache_error_result(self):
        """错误结果不应缓存."""
        error_result = {"error": "读取失败"}
        with (
            patch("src.tools.external.zhipu_web_reader._get_api_key", return_value="key"),
            patch(
                "src.tools.external.zhipu_web_reader._execute_reader",
                return_value=error_result,
            ),
            patch(
                "src.tools.external.zhipu_web_reader.get_expert_cache",
            ) as mock_cache_fn,
        ):
            mock_cache = AsyncMock()
            mock_cache.get_fetch.return_value = None
            mock_cache_fn.return_value = mock_cache

            result = await zhipu_web_reader("https://example.com")

        assert "error" in result
        mock_cache.set_fetch.assert_not_called()


class TestExtractFromCodeBlock:
    def test_should_extract_content_from_code_block(self):
        """应正确提取代码块内的内容."""
        content = "```markdown\n这是内容\n第二行\n```"
        result = _extract_from_code_block(content)
        assert "这是内容" in result
        assert "第二行" in result

    def test_should_return_original_when_no_code_block(self):
        """无代码块包裹时应返回原始内容."""
        content = "普通内容, 没有代码块"
        result = _extract_from_code_block(content)
        assert result == "普通内容, 没有代码块"

    def test_should_handle_code_block_without_language(self):
        """代码块无语言标记时应正确提取."""
        content = "```\n纯文本内容\n```"
        result = _extract_from_code_block(content)
        assert "纯文本内容" in result


class TestExecuteReader:
    @pytest.mark.asyncio
    async def test_should_skip_unreachable_url_before_api_call(self):
        """URL 预检不可达时, 应直接返回错误且不调智谱 API."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock()

        with (
            patch("src.tools.external.zhipu_web_reader._get_api_key", return_value="key"),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "src.tools.external.zhipu_web_reader._check_url_reachable",
                return_value=(False, "目标页面不存在 (404)", True),
            ),
        ):
            result = await _execute_reader(
                "key",
                "https://bad-url.com",
                return_format="markdown",
                timeout=30.0,
            )

        assert "error" in result
        assert "目标页面不存在 (404)" in result["error"]
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_not_retry_on_zhipu_500(self):
        """智谱 reader 返回 500 时不应重试, 因其多为上游错误封装."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server error",
            request=MagicMock(),
            response=mock_response,
        )

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("src.tools.external.zhipu_web_reader._get_api_key", return_value="key"),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.external.zhipu_web_reader._get_retry_params") as mock_retry,
            patch(
                "src.tools.external.zhipu_web_reader._check_url_reachable",
                return_value=(True, "", False),
            ),
        ):
            # 配置里包含 500, 但 _execute_reader 应将其排除
            mock_retry.return_value = {
                "max_retries": 2,
                "base_delay": 1.0,
                "rate_limit_delay": 3.0,
                "retryable_status": {429, 500, 502, 503, 504},
            }
            result = await _execute_reader(
                "key",
                "https://example.com",
                return_format="markdown",
                timeout=30.0,
            )

        assert "error" in result
        # 500 被排除在可重试状态码外, 只请求 1 次
        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_should_log_exception_type_on_empty_message_error(self, caplog):
        """异常 str() 为空时, 日志应携带异常类型名以保留诊断信息."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        # ValueError() 的 str() 为空, 复刻线上"冒号后无内容"的日志场景
        mock_client.post = AsyncMock(side_effect=ValueError())

        with (
            patch("src.tools.external.zhipu_web_reader._get_api_key", return_value="key"),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.external.zhipu_web_reader._get_retry_params") as mock_retry,
            patch(
                "src.tools.external.zhipu_web_reader._check_url_reachable",
                return_value=(True, "", False),
            ),
        ):
            mock_retry.return_value = {
                "max_retries": 2,
                "base_delay": 1.0,
                "rate_limit_delay": 3.0,
                "retryable_status": {429, 500, 502, 503, 504},
            }
            with caplog.at_level(logging.WARNING):
                result = await _execute_reader(
                    "key",
                    "https://example.com",
                    return_format="markdown",
                    timeout=30.0,
                )

        assert "error" in result
        # 即使 str(e) 为空, 日志也必须含异常类型名
        assert any("ValueError" in r.message for r in caplog.records)


class TestCheckUrlReachable:
    @pytest.mark.asyncio
    async def test_should_return_hard_fail_for_404(self):
        """404 页面应标记为硬失败, 跳过智谱 API."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            reachable, reason, is_hard_fail = await _check_url_reachable(
                "https://example.com/bad"
            )

        assert reachable is False
        assert "404" in reason
        assert is_hard_fail is True

    @pytest.mark.asyncio
    async def test_should_return_soft_fail_for_cloudflare(self):
        """Cloudflare 验证页应为软失败, 仍让智谱 reader 尝试."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Just a moment..."

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            reachable, reason, is_hard_fail = await _check_url_reachable(
                "https://example.com"
            )

        assert reachable is False
        assert "CDN" in reason
        assert is_hard_fail is False

    @pytest.mark.asyncio
    async def test_should_downgrade_to_get_on_405(self):
        """HEAD 405 时应降级 GET."""
        head_response = MagicMock()
        head_response.status_code = 405
        head_response.text = ""
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.text = "<html>ok</html>"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=head_response)
        mock_client.get = AsyncMock(return_value=get_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            reachable, reason, is_hard_fail = await _check_url_reachable(
                "https://example.com"
            )

        assert reachable is True
        assert reason == ""
        assert is_hard_fail is False
        mock_client.get.assert_called_once()
