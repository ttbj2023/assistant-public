"""GeoResearch 专家工具编排集成测试.

验证 geo_research/service.py 的编排逻辑, 补充单元测试未覆盖的跨组件协作场景:

- quick 模式 grounding-only: 不创建 Agent
- grounding 失败快速降级: 不创建 Agent
- deep 模式 Agent 编排: grounding 上下文注入 prompt
- deep 模式超时优雅降级: 不崩溃, 返回友好提示

测试策略: 灰盒 - Mock 外部依赖(Gemini Maps/LLM/Agent), 保留真实编排逻辑.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_grounding(answer="grounding答案", sources=None):
    """构造正常 grounding 返回值."""
    return {"answer": answer, "sources": sources or []}


def _make_mock_retry_config():
    """构造 mock 重试配置, 供 ModelRetryMiddleware 构造使用."""
    mock = MagicMock()
    mock.expert_agent.max_retries = 3
    mock.expert_agent.initial_delay = 1.0
    mock.expert_agent.max_delay = 10.0
    return mock


@pytest.mark.integration
class TestGeoResearchOrchestrationIntegration:
    """GeoResearch deep/quick 模式编排集成测试."""

    @pytest.mark.asyncio
    async def test_quick_mode_grounding_only(self):
        """测试 quick 模式: 仅 grounding, 不创建 Agent.

        协作场景: gemini_maps_grounding + _format_grounding_result
        设计思路: Mock grounding 返回结果, 验证 quick 模式不调用 create_agent
        业务价值: quick 模式应快速返回, 不启动重量级 Agent
        """
        from src.tools.experts.geo_research import service as geo_service

        grounding = _make_grounding(
            answer="距离10公里, 驾车约20分钟",
            sources=[{"title": "百度地图", "uri": "https://map.baidu.com"}],
        )

        with (
            patch.object(
                geo_service,
                "gemini_maps_grounding",
                AsyncMock(return_value=grounding),
            ),
            patch.object(geo_service, "create_agent") as mock_create_agent,
        ):
            result = await geo_service.run_geo_research(query="从A到B", depth="quick")

        assert result["depth"] == "quick"
        assert "距离10公里" in result["result"]
        assert "百度地图" in result["result"]
        mock_create_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_quick_grounding_failure_falls_back(self):
        """测试 quick 模式 grounding 失败: 降级到 place_search fallback, 不创建 Agent.

        协作场景: gemini_maps_grounding 返回 error → maps_fallback(place_search + LLM)
        设计思路: Mock grounding 失败, Mock maps_fallback 返回成功, 验证不创建 Agent
        业务价值: Gemini Maps 不可用时由等效地图工具兜底, 不直接报错
        """
        from src.tools.experts.geo_research import service as geo_service

        failed_grounding = {"answer": "", "error": "API配额超限"}

        with (
            patch.object(
                geo_service,
                "gemini_maps_grounding",
                AsyncMock(return_value=failed_grounding),
            ),
            patch.object(
                geo_service,
                "maps_fallback",
                AsyncMock(
                    return_value={
                        "result": "fallback综合结果",
                        "query": "测试",
                        "depth": "quick",
                        "language": "zh",
                        "tools_used": ["place_search"],
                        "elapsed_seconds": 0.0,
                    }
                ),
            ),
            patch.object(geo_service, "create_agent") as mock_create_agent,
        ):
            result = await geo_service.run_geo_research(query="测试", depth="quick")

        assert result["result"] == "fallback综合结果"
        assert "error" not in result
        mock_create_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_deep_mode_grounding_context_injected_into_prompt(self):
        """测试 deep 模式 Agent 编排: grounding 上下文注入 prompt.

        协作场景: create_geo_sub_tools + ExpertModelFactory + create_agent + agent.ainvoke
        设计思路: Mock 全部外部依赖, 捕获传入 agent 的 prompt, 验证 grounding 上下文被注入
        业务价值: 确保 Agent 能看到 Gemini 初步结果, 做增量补充而非从零开始
        """
        from src.tools.experts.geo_research import service as geo_service

        grounding = _make_grounding(answer="距离10公里", sources=[{"title": "起点"}])

        captured_prompt = []

        async def fake_ainvoke(input_dict, config=None):
            captured_prompt.append(input_dict["messages"][0].content)
            return {"messages": [MagicMock(content="综合回答: 最佳路线")]}

        mock_agent = MagicMock()
        mock_agent.ainvoke = fake_ainvoke

        with (
            patch.object(
                geo_service,
                "gemini_maps_grounding",
                AsyncMock(return_value=grounding),
            ),
            patch.object(
                geo_service, "create_geo_sub_tools", return_value=[MagicMock()]
            ),
            patch.object(geo_service, "ExpertModelFactory"),
            patch.object(geo_service, "create_agent", return_value=mock_agent),
            patch.object(geo_service, "enable_tool_error_handling"),
            patch.object(
                geo_service,
                "get_retry_config",
                return_value=_make_mock_retry_config(),
            ),
        ):
            result = await geo_service.run_geo_research(
                query="从A到B怎么走", depth="deep"
            )

        assert result["depth"] == "deep"
        assert result["result"] == "综合回答: 最佳路线"
        assert "maps_grounding" in result["tools_used"]
        assert "geo_sub_tools" in result["tools_used"]
        assert len(captured_prompt) == 1
        assert "从A到B怎么走" in captured_prompt[0]
        assert "距离10公里" in captured_prompt[0]

    @pytest.mark.asyncio
    async def test_deep_mode_timeout_graceful(self):
        """测试 deep 模式超时: 优雅返回超时错误.

        协作场景: agent.ainvoke → asyncio.wait_for → except TimeoutError
        设计思路: Mock agent.ainvoke 抛出 TimeoutError, 验证 except 分支正确处理
        业务价值: 超时不崩溃, 返回友好提示供 LLM 转达用户
        """
        from src.tools.experts.geo_research import service as geo_service

        grounding = _make_grounding(answer="初步结果")

        async def fake_ainvoke_timeout(input_dict, config=None):
            raise TimeoutError()

        mock_agent = MagicMock()
        mock_agent.ainvoke = fake_ainvoke_timeout

        with (
            patch.object(
                geo_service,
                "gemini_maps_grounding",
                AsyncMock(return_value=grounding),
            ),
            patch.object(geo_service, "create_geo_sub_tools", return_value=[]),
            patch.object(geo_service, "ExpertModelFactory"),
            patch.object(geo_service, "create_agent", return_value=mock_agent),
            patch.object(geo_service, "enable_tool_error_handling"),
            patch.object(
                geo_service,
                "get_retry_config",
                return_value=_make_mock_retry_config(),
            ),
        ):
            result = await geo_service.run_geo_research(query="测试", depth="deep")

        assert result["error"] == "timeout"
        assert "超时" in result["result"]
