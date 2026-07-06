"""web_research service层单元测试 - 验证语义缓存集成.

测试范围:
1. deep模式缓存命中 → grounding和agent均不调用
2. deep模式缓存未命中 → 正常执行后写入缓存
3. deep模式不缓存错误结果
4. quick模式不查语义缓存

Mock策略: Mock semantic_cache和gemini_grounding_search, 避免真实API调用.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.experts.web_research.service import run_web_research

# 成功的agent研究结果
_SUCCESS_RESULT = {
    "result": "### 核心发现\n- Go 1.23发布于2024年8月",
    "query": "Go 1.23 release notes",
    "depth": "deep",
    "language": "zh",
    "tools_used": ["doubao_search", "fetch_webpage"],
}

# 成功的grounding结果
_SUCCESS_GROUNDING = {
    "answer": "Go 1.23 released in August 2024",
    "sources": [{"domain": "go.dev"}],
    "search_queries": ["Go 1.23 release notes"],
    "source": "grounding_search",
}

# 失败的grounding结果
_FAILED_GROUNDING = {
    "answer": "",
    "error": "api_error",
    "sources": [],
    "search_queries": [],
    "source": "grounding_search",
}

_SUCCESS_URL_CONTEXT = {
    "answer": "URL Context 提取到 Go 1.23 的页面证据",
    "sources": [
        {
            "title": "Go Release",
            "url": "https://go.dev/doc/go1.23",
            "start_index": 0,
            "end_index": 8,
        }
    ],
    "retrievals": [{"url": "https://go.dev/doc/go1.23", "status": "success"}],
    "citation_count": 1,
    "verified": True,
    "source": "url_context",
}


class TestDeepCacheHit:
    """deep模式缓存命中."""

    @pytest.mark.asyncio
    @patch("src.tools.experts.web_research.service.get_semantic_cache")
    async def test_should_return_cached_result_on_hit(self, mock_get_cache):
        """缓存命中时跳过grounding和agent."""
        cached_value = json.dumps(
            {k: v for k, v in _SUCCESS_RESULT.items() if k != "elapsed_seconds"},
            ensure_ascii=False,
        )

        mock_cache = AsyncMock()
        mock_cache.get.return_value = cached_value
        mock_get_cache.return_value = mock_cache

        result = await run_web_research("Go 1.23 release notes", depth="deep")

        assert result["result"] == _SUCCESS_RESULT["result"]
        assert result.get("cache_hit") is True
        # 验证grounding未被调用 (mock_cache.get命中后直接返回)
        mock_cache.get.assert_called_once_with("Go 1.23 release notes")

    @pytest.mark.asyncio
    @patch("src.tools.experts.web_research.service.get_semantic_cache")
    async def test_should_have_elapsed_seconds_on_hit(self, mock_get_cache):
        """缓存命中时elapsed_seconds应为实际查询时间(接近0)."""
        mock_cache = AsyncMock()
        mock_cache.get.return_value = json.dumps(_SUCCESS_RESULT, ensure_ascii=False)
        mock_get_cache.return_value = mock_cache

        result = await run_web_research("test", depth="deep")
        assert result["elapsed_seconds"] < 1.0


class TestDeepCacheMiss:
    """deep模式缓存未命中."""

    @pytest.mark.asyncio
    @patch("src.tools.experts.web_research.service.get_semantic_cache")
    @patch("src.tools.experts.web_research.service._run_deep_research")
    @patch("src.tools.experts.web_research.service.gemini_url_context")
    @patch("src.tools.experts.web_research.service.gemini_grounding_search")
    async def test_should_execute_and_cache_on_miss(
        self,
        mock_grounding,
        mock_url_context,
        mock_deep_research,
        mock_get_cache,
    ):
        """缓存未命中时正常执行agent并写入缓存."""
        mock_grounding.return_value = _SUCCESS_GROUNDING
        mock_url_context.return_value = _SUCCESS_URL_CONTEXT
        mock_deep_research.return_value = _SUCCESS_RESULT.copy()

        mock_cache = AsyncMock()
        mock_cache.get.return_value = None  # 缓存未命中
        mock_get_cache.return_value = mock_cache

        result = await run_web_research("Go 1.23 release notes", depth="deep")

        # 验证执行结果正确
        assert result["result"] == _SUCCESS_RESULT["result"]
        # 验证grounding被调用
        mock_grounding.assert_called_once()
        # 无显式URL时不再对grounding死链源做URL Context深读(路径已移除)
        mock_url_context.assert_not_called()
        # 验证agent被调用
        mock_deep_research.assert_called_once()
        # 无显式URL → url_context为None
        assert mock_deep_research.call_args.kwargs["url_context"] is None
        # 验证缓存写入
        mock_cache.put.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.tools.experts.web_research.service.get_semantic_cache")
    @patch("src.tools.experts.web_research.service._run_deep_research")
    @patch("src.tools.experts.web_research.service.gemini_url_context")
    @patch("src.tools.experts.web_research.service.gemini_grounding_search")
    async def test_should_not_cache_error_result(
        self,
        mock_grounding,
        mock_url_context,
        mock_deep_research,
        mock_get_cache,
    ):
        """agent返回错误时不写入缓存."""
        mock_grounding.return_value = _SUCCESS_GROUNDING
        mock_url_context.return_value = _SUCCESS_URL_CONTEXT

        error_result = {
            "result": "研究操作超时(300秒)",
            "query": "test",
            "depth": "deep",
            "language": "zh",
            "elapsed_seconds": 300.0,
            "error": "timeout",
        }
        mock_deep_research.return_value = error_result

        mock_cache = AsyncMock()
        mock_cache.get.return_value = None
        mock_get_cache.return_value = mock_cache

        result = await run_web_research("test", depth="deep")

        assert "error" in result
        # 验证缓存未被写入
        mock_cache.put.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.tools.experts.web_research.service.get_semantic_cache")
    @patch("src.tools.experts.web_research.service._run_deep_research")
    @patch("src.tools.experts.web_research.service.gemini_url_context")
    @patch("src.tools.experts.web_research.service.gemini_grounding_search")
    async def test_gather_isolates_url_context_failure(
        self,
        mock_grounding,
        mock_url_context,
        mock_deep_research,
        mock_get_cache,
    ):
        """deep+显式URL 时 url_context 抛异常不应牵连 grounding (gather 异常隔离).

        回归 return_exceptions=True: 并发任务之一失败时另一个结果仍可用,
        url_context 异常降级为 None, 不再向上传播导致整个 research 崩溃.
        """
        mock_grounding.return_value = _SUCCESS_GROUNDING
        mock_url_context.side_effect = RuntimeError("url_context boom")
        mock_deep_research.return_value = _SUCCESS_RESULT.copy()

        mock_cache = AsyncMock()
        mock_cache.get.return_value = None
        mock_get_cache.return_value = mock_cache

        result = await run_web_research(
            "分析 https://go.dev/doc/go1.23", depth="deep"
        )

        # url_context 异常被隔离, grounding 结果未丢失, 深度研究正常完成
        mock_grounding.assert_called_once()
        assert result["result"] == _SUCCESS_RESULT["result"]
        # url_context 降级为 None 传入 deep research
        assert mock_deep_research.call_args.kwargs["url_context"] is None


class TestQuickMode:
    """quick模式不受deep缓存影响."""

    @pytest.mark.asyncio
    @patch("src.tools.experts.web_research.service.get_semantic_cache")
    @patch("src.tools.experts.web_research.service.gemini_grounding_search")
    async def test_quick_mode_should_not_check_cache(
        self,
        mock_grounding,
        mock_get_cache,
    ):
        """quick模式不查语义缓存."""
        mock_grounding.return_value = _SUCCESS_GROUNDING

        mock_cache = AsyncMock()
        mock_get_cache.return_value = mock_cache

        result = await run_web_research("test query", depth="quick")

        # quick模式不应调用语义缓存
        mock_cache.get.assert_not_called()
        # grounding被调用
        mock_grounding.assert_called_once()
        # 结果包含grounding内容
        assert result["depth"] == "quick"

    @pytest.mark.asyncio
    @patch("src.tools.experts.web_research.service.gemini_grounding_search")
    @patch("src.tools.experts.web_research.service.gemini_url_context")
    async def test_quick_mode_with_url_should_use_url_context(
        self,
        mock_url_context,
        mock_grounding,
    ):
        """quick模式 query 自带 URL 时应优先使用 URL Context."""
        mock_url_context.return_value = _SUCCESS_URL_CONTEXT

        result = await run_web_research(
            "比较 https://go.dev/doc/go1.23 的更新",
            depth="quick",
        )

        assert result["depth"] == "quick"
        assert "URL Context 提取到" in result["result"]
        assert result["tools_used"] == ["url_context"]
        mock_url_context.assert_called_once()
        mock_grounding.assert_not_called()

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.web_research.service._fallback_enabled", return_value=True
    )
    @patch("src.tools.experts.web_research.service.url_context_fallback")
    @patch("src.tools.experts.web_research.service.gemini_grounding_search")
    @patch("src.tools.experts.web_research.service.gemini_url_context")
    async def test_quick_mode_unverified_url_context_should_fallback(
        self,
        mock_url_context,
        mock_grounding,
        mock_url_fallback,
        mock_fallback_enabled,
    ):
        """URL Context 无 citation 时 quick 模式降级到 zhipu_reader fallback."""
        mock_url_context.return_value = {
            "answer": "无引用回答",
            "sources": [],
            "retrievals": [{"url": "https://example.com", "status": "success"}],
            "verified": False,
            "error": "no_url_citation",
        }
        mock_url_fallback.return_value = {
            "result": "fallback 综合结果",
            "query": "总结 https://example.com",
            "depth": "quick",
            "language": "zh",
            "tools_used": ["zhipu_reader"],
            "elapsed_seconds": 0.0,
        }

        result = await run_web_research(
            "总结 https://example.com",
            depth="quick",
        )

        mock_url_fallback.assert_called_once()
        assert result["result"] == "fallback 综合结果"
        mock_grounding.assert_not_called()


class TestGroundingFailure:
    """Grounding失败的降级处理."""

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.web_research.service._fallback_enabled",
        return_value=False,
    )
    @patch("src.tools.experts.web_research.service.get_semantic_cache")
    @patch("src.tools.experts.web_research.service.gemini_grounding_search")
    async def test_should_return_error_when_fallback_disabled(
        self,
        mock_grounding,
        mock_get_cache,
        mock_fallback,
    ):
        """fallback关闭时, Grounding失败直接返回错误."""
        mock_grounding.return_value = _FAILED_GROUNDING
        mock_cache = AsyncMock()
        mock_cache.get.return_value = None
        mock_get_cache.return_value = mock_cache

        result = await run_web_research("test", depth="deep")

        assert "error" in result
        assert result["error"] == "grounding_failed"
        mock_cache.put.assert_not_called()

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.web_research.service._fallback_enabled",
        return_value=True,
    )
    @patch("src.tools.experts.web_research.service.get_semantic_cache")
    @patch("src.tools.experts.web_research.service._run_deep_research")
    @patch("src.tools.experts.web_research.service.gemini_grounding_search")
    async def test_deep_grounding_failure_should_enter_agent(
        self,
        mock_grounding,
        mock_deep_research,
        mock_get_cache,
        mock_fallback,
    ):
        """fallback启用时, deep模式Grounding失败置空grounding直接进Agent."""
        mock_grounding.return_value = _FAILED_GROUNDING
        mock_deep_research.return_value = _SUCCESS_RESULT.copy()
        mock_cache = AsyncMock()
        mock_cache.get.return_value = None
        mock_get_cache.return_value = mock_cache

        result = await run_web_research("test", depth="deep")

        assert "error" not in result
        mock_deep_research.assert_called_once()
        # grounding 置空后作为位置参数传给 _run_deep_research
        assert mock_deep_research.call_args.args[1] is None

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.web_research.service._fallback_enabled",
        return_value=True,
    )
    @patch("src.tools.experts.web_research.service.search_fallback")
    @patch("src.tools.experts.web_research.service.gemini_grounding_search")
    async def test_quick_grounding_failure_should_fallback(
        self,
        mock_grounding,
        mock_search_fallback,
        mock_fallback,
    ):
        """fallback启用时, quick模式Grounding失败降级到doubao_search."""
        mock_grounding.return_value = _FAILED_GROUNDING
        mock_search_fallback.return_value = {
            "result": "fallback综合结果",
            "query": "test",
            "depth": "quick",
            "language": "zh",
            "tools_used": ["doubao_search"],
            "elapsed_seconds": 0.0,
        }

        result = await run_web_research("test", depth="quick")

        mock_search_fallback.assert_called_once()
        assert result["result"] == "fallback综合结果"

    @pytest.mark.asyncio
    @patch("src.tools.experts.web_research.service.get_semantic_cache")
    @patch("src.tools.experts.web_research.service._run_deep_research")
    @patch("src.tools.experts.web_research.service.gemini_url_context")
    @patch("src.tools.experts.web_research.service.gemini_grounding_search")
    async def test_should_continue_when_url_context_verified(
        self,
        mock_grounding,
        mock_url_context,
        mock_deep_research,
        mock_get_cache,
    ):
        """显式 URL 场景: grounding失败但URL Context可靠时继续研究."""
        mock_grounding.return_value = _FAILED_GROUNDING
        mock_url_context.return_value = _SUCCESS_URL_CONTEXT
        mock_deep_research.return_value = _SUCCESS_RESULT.copy()
        mock_cache = AsyncMock()
        mock_cache.get.return_value = None
        mock_get_cache.return_value = mock_cache

        result = await run_web_research(
            "分析 https://go.dev/doc/go1.23",
            depth="deep",
        )

        assert "error" not in result
        assert result["result"] == _SUCCESS_RESULT["result"]
        assert (
            mock_deep_research.call_args.kwargs["url_context"] == _SUCCESS_URL_CONTEXT
        )
