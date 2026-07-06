"""geo_research service 层单元测试 - 验证 Maps Grounding fallback 集成.

测试范围:
1. quick 模式 grounding 成功 → 正常返回
2. quick 模式 grounding 失败 + fallback 启用 → maps_fallback 降级
3. quick 模式 grounding 失败 + fallback 关闭 → 返回错误
4. deep 模式 grounding 失败 + fallback 启用 → 置空 grounding 进 Agent
5. deep 模式 grounding 失败 + fallback 关闭 → 返回错误

Mock策略: Mock gemini_maps_grounding / maps_fallback / _run_deep, 避免真实调用.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.tools.experts.geo_research.service import run_geo_research

_SUCCESS_GROUNDING = {
    "answer": "故宫位于北京中心",
    "sources": [{"title": "故宫博物院", "uri": "https://amap/place/1"}],
    "search_queries": [],
    "maps_chunks_count": 1,
    "source": "maps_grounding",
}

_FAILED_GROUNDING = {
    "answer": "",
    "error": "429 RESOURCE_EXHAUSTED",
    "sources": [],
    "search_queries": [],
    "maps_chunks_count": 0,
    "source": "maps_grounding",
}

_FB_RESULT = {
    "result": "fallback 综合答案",
    "query": "故宫",
    "depth": "quick",
    "language": "zh",
    "tools_used": ["place_search", "llm_synthesis"],
    "elapsed_seconds": 0.0,
}

_DEEP_RESULT = {
    "result": "deep 综合答案",
    "query": "故宫",
    "depth": "deep",
    "language": "zh",
    "elapsed_seconds": 5.0,
    "tools_used": ["maps_grounding", "geo_sub_tools"],
}


class TestQuickMode:
    """quick 模式."""

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.geo_research.service.gemini_maps_grounding",
        new_callable=AsyncMock,
    )
    async def test_quick_success(self, mock_grounding):
        """grounding 成功时正常返回."""
        mock_grounding.return_value = _SUCCESS_GROUNDING

        result = await run_geo_research("故宫", depth="quick")

        assert result["depth"] == "quick"
        assert "故宫位于北京中心" in result["result"]
        assert result["tools_used"] == ["maps_grounding"]


class TestQuickFallback:
    """quick 模式 grounding 失败的降级."""

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.geo_research.service._fallback_enabled",
        return_value=True,
    )
    @patch(
        "src.tools.experts.geo_research.service.maps_fallback",
        new_callable=AsyncMock,
    )
    @patch(
        "src.tools.experts.geo_research.service.gemini_maps_grounding",
        new_callable=AsyncMock,
    )
    async def test_quick_failure_should_fallback(
        self, mock_grounding, mock_maps_fallback, mock_fallback
    ):
        """fallback 启用时, quick grounding 失败降级到 place_search."""
        mock_grounding.return_value = _FAILED_GROUNDING
        mock_maps_fallback.return_value = _FB_RESULT

        result = await run_geo_research("故宫", depth="quick")

        mock_maps_fallback.assert_called_once()
        assert result["result"] == "fallback 综合答案"

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.geo_research.service._fallback_enabled",
        return_value=False,
    )
    @patch(
        "src.tools.experts.geo_research.service.gemini_maps_grounding",
        new_callable=AsyncMock,
    )
    async def test_quick_failure_fallback_disabled(self, mock_grounding, mock_fallback):
        """fallback 关闭时, quick grounding 失败返回错误."""
        mock_grounding.return_value = _FAILED_GROUNDING

        result = await run_geo_research("故宫", depth="quick")

        assert result["error"] == "grounding_failed"


class TestDeepFallback:
    """deep 模式 grounding 失败的降级."""

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.geo_research.service._fallback_enabled",
        return_value=True,
    )
    @patch(
        "src.tools.experts.geo_research.service._run_deep",
        new_callable=AsyncMock,
    )
    @patch(
        "src.tools.experts.geo_research.service.gemini_maps_grounding",
        new_callable=AsyncMock,
    )
    async def test_deep_failure_should_enter_agent(
        self, mock_grounding, mock_run_deep, mock_fallback
    ):
        """fallback 启用时, deep grounding 失败置空 grounding 直接进 Agent."""
        mock_grounding.return_value = _FAILED_GROUNDING
        mock_run_deep.return_value = _DEEP_RESULT

        result = await run_geo_research("故宫", depth="deep")

        assert "error" not in result
        mock_run_deep.assert_called_once()
        # grounding 置空作为位置参数传给 _run_deep
        assert mock_run_deep.call_args.args[1] is None

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.geo_research.service._fallback_enabled",
        return_value=False,
    )
    @patch(
        "src.tools.experts.geo_research.service.gemini_maps_grounding",
        new_callable=AsyncMock,
    )
    async def test_deep_failure_fallback_disabled(self, mock_grounding, mock_fallback):
        """fallback 关闭时, deep grounding 失败返回错误."""
        mock_grounding.return_value = _FAILED_GROUNDING

        result = await run_geo_research("故宫", depth="deep")

        assert result["error"] == "grounding_failed"

    @pytest.mark.asyncio
    @patch(
        "src.tools.experts.geo_research.service._run_deep",
        new_callable=AsyncMock,
    )
    @patch(
        "src.tools.experts.geo_research.service.gemini_maps_grounding",
        new_callable=AsyncMock,
    )
    async def test_deep_success_enters_agent_with_grounding(
        self, mock_grounding, mock_run_deep
    ):
        """grounding 成功时 deep 模式正常带 grounding 进 Agent."""
        mock_grounding.return_value = _SUCCESS_GROUNDING
        mock_run_deep.return_value = _DEEP_RESULT

        result = await run_geo_research("故宫", depth="deep")

        assert "error" not in result
        mock_run_deep.assert_called_once()
        # grounding 成功时作为有效 dict 传入
        assert mock_run_deep.call_args.args[1] == _SUCCESS_GROUNDING
