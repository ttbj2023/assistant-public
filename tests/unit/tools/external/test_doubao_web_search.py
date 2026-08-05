"""DoubaoSearchTool 单元测试.

测试豆包网络搜索工具的核心逻辑, Mock外部API调用.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.tools.external.doubao_web_search import (
    DoubaoSearchTool,
    _execute_search,
    _format_search_results,
    doubao_web_search,
)


@pytest.fixture
def search_tool():
    """创建豆包搜索工具实例."""
    return DoubaoSearchTool()


class TestDoubaoSearchToolAvailability:
    @pytest.mark.asyncio
    async def test_is_available_should_return_true_when_key_set(self):
        """API Key存在时应返回True."""
        tool = DoubaoSearchTool()
        with patch(
            "src.tools.external.doubao_web_search._get_api_key",
            return_value="test_key",
        ):
            result = await tool.is_available()
            assert result is True

    @pytest.mark.asyncio
    async def test_is_available_should_return_false_when_key_missing(self):
        """API Key不存在时应返回False."""
        tool = DoubaoSearchTool()
        with patch(
            "src.tools.external.doubao_web_search._get_api_key",
            return_value="",
        ):
            result = await tool.is_available()
            assert result is False


class TestDoubaoSearchToolRun:
    @pytest.mark.asyncio
    async def test_arun_should_return_formatted_results(self, search_tool):
        """成功搜索应返回格式化文本."""
        mock_result = {
            "search_results": [
                {
                    "title": "AI大模型最新进展",
                    "link": "https://example.com/ai",
                    "content": "2026年AI领域的重大突破",
                    "publish_date": "2026-06-01",
                    "auth_info": "非常权威",
                },
            ],
        }

        with patch(
            "src.tools.external.doubao_web_search.doubao_web_search",
            return_value=mock_result,
        ):
            result = await search_tool._arun(query="AI大模型", count=5)

        assert "AI大模型最新进展" in result
        assert "https://example.com/ai" in result
        assert "非常权威" in result

    @pytest.mark.asyncio
    async def test_arun_should_return_error_json_on_failure(self, search_tool):
        """搜索失败应返回错误JSON."""
        error_result = {"error": "搜索失败: 网络超时"}

        with patch(
            "src.tools.external.doubao_web_search.doubao_web_search",
            return_value=error_result,
        ):
            result = await search_tool._arun(query="test query")

        parsed = json.loads(result)
        assert "error" in parsed


class TestDoubaoWebSearchFunction:
    @pytest.mark.asyncio
    async def test_should_return_error_when_no_api_key(self):
        """无API Key时应返回错误."""
        with patch(
            "src.tools.external.doubao_web_search._get_api_key",
            return_value="",
        ):
            result = await doubao_web_search("test query")

        assert "error" in result
        assert "ARK_AGENT_PLAN_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_should_return_cached_data(self):
        """缓存命中时直接返回缓存."""
        cached = json.dumps({
            "search_results": [
                {"title": "缓存结果", "link": "https://example.com"},
            ],
        })
        with (
            patch(
                "src.tools.external.doubao_web_search._get_api_key",
                return_value="key",
            ),
            patch(
                "src.tools.external.doubao_web_search.get_expert_cache",
            ) as mock_cache_fn,
        ):
            mock_cache = AsyncMock()
            mock_cache.get_search.return_value = cached
            mock_cache_fn.return_value = mock_cache

            result = await doubao_web_search("test query")

        assert len(result["search_results"]) == 1

    @pytest.mark.asyncio
    async def test_should_cache_successful_result(self):
        """成功搜索后应缓存结果."""
        success_result = {
            "search_results": [
                {"title": "测试", "link": "https://example.com"},
            ],
        }
        with (
            patch(
                "src.tools.external.doubao_web_search._get_api_key",
                return_value="key",
            ),
            patch(
                "src.tools.external.doubao_web_search._execute_search",
                return_value=success_result,
            ),
            patch(
                "src.tools.external.doubao_web_search.get_expert_cache",
            ) as mock_cache_fn,
        ):
            mock_cache = AsyncMock()
            mock_cache.get_search.return_value = None
            mock_cache_fn.return_value = mock_cache

            result = await doubao_web_search("test query")

        assert result["search_results"][0]["title"] == "测试"
        mock_cache.set_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_not_cache_error_result(self):
        """错误结果不应缓存."""
        error_result = {"error": "搜索失败"}
        with (
            patch(
                "src.tools.external.doubao_web_search._get_api_key",
                return_value="key",
            ),
            patch(
                "src.tools.external.doubao_web_search._execute_search",
                return_value=error_result,
            ),
            patch(
                "src.tools.external.doubao_web_search.get_expert_cache",
            ) as mock_cache_fn,
        ):
            mock_cache = AsyncMock()
            mock_cache.get_search.return_value = None
            mock_cache_fn.return_value = mock_cache

            result = await doubao_web_search("test query")

        assert "error" in result
        mock_cache.set_search.assert_not_called()


class TestExecuteSearchRetry:
    """豆包搜索 200-body 业务错误重试测试."""

    @staticmethod
    def _make_response(error_message: str | None = None, results: list | None = None) -> dict:
        if error_message:
            return {
                "ResponseMetadata": {
                    "Error": {"Message": error_message},
                },
            }
        return {
            "ResponseMetadata": {},
            "Result": {"WebResults": results or []},
        }

    @staticmethod
    def _make_fake_response(data: dict):
        """构造同步 json() 返回的 httpx Response Mock."""
        mock = Mock()
        mock.status_code = 200
        mock.json = Mock(return_value=data)
        mock.raise_for_status = Mock()
        return mock

    @pytest.mark.asyncio
    async def test_should_retry_internal_error_then_succeed(self):
        """Internal Error 业务错误应重试, 重试成功后返回结果."""
        responses = [
            self._make_response("Internal Error"),
            self._make_response(results=[{"Title": "OK", "Url": "https://example.com"}]),
        ]
        call_index = 0

        async def _fake_post(*args, **kwargs):
            nonlocal call_index
            resp_data = responses[call_index]
            call_index += 1
            return self._make_fake_response(resp_data)

        with (
            patch(
                "src.tools.external.doubao_web_search._get_api_key",
                return_value="key",
            ),
            patch("src.tools.external.doubao_web_search._get_retry_params") as mock_retry,
            patch("httpx.AsyncClient.post", side_effect=_fake_post) as mock_post,
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            mock_retry.return_value = {
                "max_retries": 2,
                "base_delay": 1.0,
                "rate_limit_delay": 3.0,
                "retryable_status": {429, 500, 502, 503, 504},
            }

            result = await _execute_search("key", "test", count=5, timeout=30.0)

        assert "error" not in result
        assert result["search_results"][0]["title"] == "OK"
        assert mock_post.call_count == 2
        mock_sleep.assert_awaited_once_with(1.0)

    @pytest.mark.asyncio
    async def test_should_return_error_after_max_retries(self):
        """Internal Error 持续存在时, 耗尽重试后返回错误."""
        resp = self._make_response("Internal Error")

        async def _fake_post(*args, **kwargs):
            return self._make_fake_response(resp)

        with (
            patch(
                "src.tools.external.doubao_web_search._get_api_key",
                return_value="key",
            ),
            patch("src.tools.external.doubao_web_search._get_retry_params") as mock_retry,
            patch("httpx.AsyncClient.post", side_effect=_fake_post) as mock_post,
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            mock_retry.return_value = {
                "max_retries": 2,
                "base_delay": 1.0,
                "rate_limit_delay": 3.0,
                "retryable_status": {429, 500, 502, 503, 504},
            }

            result = await _execute_search("key", "test", count=5, timeout=30.0)

        assert "error" in result
        assert "Internal Error" in result["error"]
        assert mock_post.call_count == 2
        assert mock_sleep.await_count == 1

    @pytest.mark.asyncio
    async def test_should_not_retry_non_internal_business_error(self):
        """非 internal 类业务错误不应重试, 直接返回错误."""
        resp = self._make_response("Authentication failed")

        async def _fake_post(*args, **kwargs):
            return self._make_fake_response(resp)

        with (
            patch(
                "src.tools.external.doubao_web_search._get_api_key",
                return_value="key",
            ),
            patch("src.tools.external.doubao_web_search._get_retry_params") as mock_retry,
            patch("httpx.AsyncClient.post", side_effect=_fake_post) as mock_post,
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            mock_retry.return_value = {
                "max_retries": 2,
                "base_delay": 1.0,
                "rate_limit_delay": 3.0,
                "retryable_status": {429, 500, 502, 503, 504},
            }

            result = await _execute_search("key", "test", count=5, timeout=30.0)

        assert "error" in result
        assert "Authentication failed" in result["error"]
        assert mock_post.call_count == 1
        mock_sleep.assert_not_awaited()


class TestFormatSearchResults:
    def test_should_format_results_with_all_fields(self):
        """完整字段时应格式化输出."""
        result = {
            "search_results": [
                {
                    "title": "AI发展报告",
                    "link": "https://example.com",
                    "content": "这是搜索结果摘要内容",
                    "publish_date": "2026-06-01",
                    "auth_info": "正常权威",
                },
            ],
        }
        formatted = _format_search_results(result)

        assert "AI发展报告" in formatted
        assert "https://example.com" in formatted
        assert "2026-06-01" in formatted
        assert "搜索结果摘要内容" in formatted
        assert "正常权威" in formatted

    def test_should_handle_empty_results(self):
        """无结果时应返回提示信息."""
        result = {"search_results": []}
        formatted = _format_search_results(result)

        assert "未找到" in formatted

    def test_should_truncate_long_content(self):
        """过长内容应截断."""
        long_content = "a" * 500
        result = {
            "search_results": [
                {
                    "title": "测试",
                    "link": "",
                    "content": long_content,
                },
            ],
        }
        formatted = _format_search_results(result)

        assert "..." in formatted

    def test_should_limit_display_results(self):
        """应限制显示数量并提示剩余."""
        result = {
            "search_results": [
                {"title": f"结果{i}", "link": "", "content": ""}
                for i in range(8)
            ],
        }
        formatted = _format_search_results(result)

        assert "还有 3 个结果未显示" in formatted

    def test_should_skip_empty_fields(self):
        """空字段应跳过不显示."""
        result = {
            "search_results": [
                {
                    "title": "只有标题",
                    "link": "",
                    "content": "",
                    "publish_date": "",
                    "auth_info": "",
                },
            ],
        }
        formatted = _format_search_results(result)

        assert "只有标题" in formatted
        assert "链接" not in formatted
        assert "日期" not in formatted
        assert "权威度" not in formatted

    def test_should_show_count_header(self):
        """应显示搜索结果数量."""
        result = {
            "search_results": [
                {"title": "结果1", "link": "", "content": ""},
                {"title": "结果2", "link": "", "content": ""},
            ],
        }
        formatted = _format_search_results(result)

        assert "找到 2 个" in formatted
