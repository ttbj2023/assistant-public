"""WebResearch 专家工具编排集成测试.

验证 web_research/service.py 的编排逻辑, 补充单元测试(test_web_research_service)和
Tool 包装层测试(test_expert_tool_wrappers)未覆盖的跨组件协作场景:

- deep 模式 MCP 白名单过滤 (_MCP_SEARCH_TOOLS): 仅 baidu_search 进入 Agent
- deep 模式语义缓存命中短路: 跳过 grounding 和 agent
- quick 模式 grounding-only: 不创建 Agent

测试策略: 灰盒 - Mock 外部依赖(Gemini grounding / Agent LLM / 语义缓存),
保留真实编排逻辑(工具集组装 / 白名单过滤 / 模式分发).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_cache(get_return=None):
    """构造语义缓存 mock, 避免真实 ChromaDB 初始化."""
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=get_return)
    cache.put = AsyncMock()
    return cache


def _make_grounding(answer="grounding答案", sources=None):
    """构造正常 grounding 返回值."""
    return {"answer": answer, "sources": sources or []}


@pytest.mark.integration
class TestWebResearchOrchestrationIntegration:
    """WebResearch deep/quick 模式编排集成测试."""

    @pytest.mark.asyncio
    async def test_deep_mode_mcp_whitelist_filters_non_search_tools(self):
        """测试 deep 模式 MCP 白名单过滤: 仅 baidu_search 进入 Agent.

        协作场景: _run_deep_research + mcp_bridge.get_all_tools + _MCP_SEARCH_TOOLS 白名单
        设计思路: Mock mcp_bridge 返回多个工具(含 baidu_search + 非白名单工具),
                  捕获传入 get_research_agent 的 tools, 验证白名单过滤生效
        业务价值: 确保只有搜索类 MCP 工具进入研究 Agent, 防止无关工具污染 Agent 行为
        """
        from src.tools.experts.web_research import service as wr_service

        baidu_search_tool = MagicMock()
        baidu_search_tool.name = "baidu_search"
        unrelated_tool = MagicMock()
        unrelated_tool.name = "file_manager"
        mock_bridge = AsyncMock()
        mock_bridge.get_all_tools = AsyncMock(
            return_value=[baidu_search_tool, unrelated_tool]
        )

        captured_tools = []

        def capture_get_agent(model_id, tools, timeout, llm_request_timeout):
            captured_tools.extend(tools)
            mock_agent = AsyncMock()
            mock_agent.research = AsyncMock(
                return_value={
                    "result": "研究完成",
                    "query": "测试",
                    "depth": "deep",
                    "language": "zh",
                    "tools_used": [],
                }
            )
            return mock_agent

        with (
            patch.object(
                wr_service, "get_semantic_cache", return_value=_make_mock_cache()
            ),
            patch.object(
                wr_service,
                "gemini_grounding_search",
                AsyncMock(return_value=_make_grounding()),
            ),
            patch.object(
                wr_service, "get_research_agent", side_effect=capture_get_agent
            ),
        ):
            result = await wr_service.run_web_research(
                query="测试查询", depth="deep", mcp_bridge=mock_bridge
            )

        assert result["depth"] == "deep"
        assert baidu_search_tool in captured_tools, (
            "白名单内的 baidu_search 应进入 Agent"
        )
        assert unrelated_tool not in captured_tools, (
            "白名单外的 file_manager 不应进入 Agent"
        )
        mcp_passed = [
            t for t in captured_tools if t in (baidu_search_tool, unrelated_tool)
        ]
        assert len(mcp_passed) == 1, "仅 1 个 MCP 工具应通过白名单"
        assert len(captured_tools) == 5, "4 本地工具 + 1 MCP 工具 = 5"

    @pytest.mark.asyncio
    async def test_deep_mode_cache_hit_skips_grounding_and_agent(self):
        """测试 deep 模式缓存命中: 跳过 grounding 和 agent.

        协作场景: get_semantic_cache().get() 命中 → 短路返回
        设计思路: Mock 语义缓存返回命中, 验证 grounding 和 agent 均未被调用
        业务价值: 缓存命中时避免昂贵的 Gemini/Agent 调用, 显著降低延迟
        """
        from src.tools.experts.web_research import service as wr_service

        cached = {
            "result": "缓存的研究结果",
            "query": "测试查询",
            "depth": "deep",
            "language": "zh",
            "tools_used": ["cached"],
        }
        mock_cache = _make_mock_cache(get_return=json.dumps(cached, ensure_ascii=False))

        with (
            patch.object(wr_service, "get_semantic_cache", return_value=mock_cache),
            patch.object(
                wr_service, "gemini_grounding_search", AsyncMock()
            ) as mock_grounding,
            patch.object(wr_service, "get_research_agent") as mock_get_agent,
        ):
            result = await wr_service.run_web_research(query="测试查询", depth="deep")

        assert result["result"] == "缓存的研究结果"
        assert result.get("cache_hit") is True
        mock_grounding.assert_not_called()
        mock_get_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_quick_mode_uses_grounding_only(self):
        """测试 quick 模式: 仅 grounding, 不创建 Agent.

        协作场景: gemini_grounding_search + _format_grounding_result (无 Agent / 无缓存)
        设计思路: Mock grounding 返回结果, 验证 quick 模式不调用 get_research_agent
        业务价值: quick 模式应快速返回(3-6秒), 不启动重量级 Agent
        """
        from src.tools.experts.web_research import service as wr_service

        grounding = _make_grounding(answer="快速答案", sources=[{"domain": "来源1"}])

        with (
            patch.object(
                wr_service,
                "gemini_grounding_search",
                AsyncMock(return_value=grounding),
            ),
            patch.object(wr_service, "get_research_agent") as mock_get_agent,
        ):
            result = await wr_service.run_web_research(query="快速查询", depth="quick")

        assert result["depth"] == "quick"
        assert "快速答案" in result["result"]
        assert "来源1" in result["result"]
        mock_get_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_deep_grounding_failure_enters_agent(self):
        """测试 deep 模式 grounding 失败: 置空 grounding 直接进 Agent 降级研究.

        协作场景: gemini_grounding_search 返回 error → fallback 启用 → 进 Agent
        设计思路: Mock grounding 返回错误, Mock Agent, 验证 Agent 被调用且 grounding_context=None
        业务价值: Gemini 不可用时由 Agent 自带工具(doubao_search 等)兜底, 不直接报错
        """
        from src.tools.experts.web_research import service as wr_service

        failed_grounding = {"answer": "", "error": "API quota exceeded"}

        mock_agent = AsyncMock()
        mock_agent.research = AsyncMock(
            return_value={
                "result": "Agent研究结果",
                "query": "测试",
                "depth": "deep",
                "language": "zh",
                "tools_used": ["doubao_search"],
            }
        )

        with (
            patch.object(
                wr_service,
                "gemini_grounding_search",
                AsyncMock(return_value=failed_grounding),
            ),
            patch.object(
                wr_service, "get_semantic_cache", return_value=_make_mock_cache()
            ),
            patch.object(
                wr_service, "get_research_agent", return_value=mock_agent
            ) as mock_get_agent,
        ):
            result = await wr_service.run_web_research(query="测试", depth="deep")

        assert "error" not in result
        assert result["result"] == "Agent研究结果"
        mock_get_agent.assert_called_once()
        # grounding 置空后传给 Agent
        assert mock_agent.research.call_args.kwargs.get("grounding_context") is None
