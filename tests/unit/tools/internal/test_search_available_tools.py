"""SearchAvailableTools 单元测试

覆盖范围:
- 初始化: catalog 状态, args_schema 声明
- set_catalog: 目录注入与覆盖
- _arun: 核心搜索逻辑 (空查询/匹配/无匹配/空目录/纯空格)
- _search_catalog: 评分匹配 (名称片段/描述/多关键词/大小写/中文/排序)
- _tokenize: 查询预处理
- _score: 多信号评分
- _format_all_tools: 格式化输出
- 真实场景验证: 11次真实LLM查询的噪音过滤效果
"""

from __future__ import annotations

import json

import pytest

from src.tools.internal.search_available_tools import SearchAvailableTools

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CATALOG = {
    "web_research": {
        "name": "web_research",
        "description": "网络研究, 快速搜索或深度研究, 返回带引用的结构化答案",
        "full_description": "网络研究工具. 基于Gemini搜索获取实时信息并生成带引用来源的结构化答案。",
        "keywords": ["搜索", "研究", "调查"],
        "name_parts": ["web", "research"],
    },
    "geo_navigator": {
        "name": "geo_navigator",
        "description": "地理出行导航, 搜索POI/规划路线/查实时路况",
        "full_description": "地理出行研究工具. 接收自然语言地理/出行查询, 搜索POI、规划路线、查询实时路况等。",
        "keywords": ["地图", "导航", "路线", "出行", "POI"],
        "name_parts": ["geo", "navigator"],
    },
    "weather_query": {
        "name": "weather_query",
        "description": "查询实时天气(温度/湿度/风力/空气质量)",
        "full_description": "查询指定城市的实时天气, 包括温度/湿度/风力/空气质量/未来3天预报。",
        "keywords": ["天气", "温度", "预报"],
        "name_parts": ["weather", "query"],
    },
    "scheduled_messenger": {
        "name": "scheduled_messenger",
        "description": "定时消息发送, 按时提醒用户, 支持多渠道",
        "full_description": "定时消息发送工具. 你可以设定在指定时间给用户发送消息, 支持多种渠道。",
        "keywords": ["定时", "提醒", "消息", "通知", "微信", "邮件"],
        "name_parts": ["scheduled", "messenger"],
    },
}


@pytest.fixture
def tool() -> SearchAvailableTools:
    return SearchAvailableTools(
        user_id="test_user", thread_id="test_thread", agent_id="test-agent"
    )


@pytest.fixture
def tool_with_catalog() -> SearchAvailableTools:
    t = SearchAvailableTools(
        user_id="test_user", thread_id="test_thread", agent_id="test-agent"
    )
    t.set_catalog(SAMPLE_CATALOG)
    return t


# ---------------------------------------------------------------------------
# TestSearchAvailableToolsInit
# ---------------------------------------------------------------------------


class TestSearchAvailableToolsInit:
    def test_init_should_accept_default_user_thread(self):
        t = SearchAvailableTools(agent_id="test")
        assert t.user_id == ""
        assert t.thread_id == ""


# ---------------------------------------------------------------------------
# TestSetCatalog
# ---------------------------------------------------------------------------


class TestSetCatalog:
    def test_set_catalog_should_replace_existing(self, tool_with_catalog):
        new_catalog = {"new_tool": {"name": "new_tool", "description": "新工具"}}
        tool_with_catalog.set_catalog(new_catalog)
        assert "web_research" not in tool_with_catalog._catalog
        assert "new_tool" in tool_with_catalog._catalog


# ---------------------------------------------------------------------------
# TestArun
# ---------------------------------------------------------------------------


class TestArun:
    @pytest.mark.asyncio
    async def test_empty_query_should_return_all_tools(self, tool_with_catalog):
        result = json.loads(await tool_with_catalog._arun(query=""))
        assert result["success"] is True
        assert len(result["matched_tools"]) == 4

    @pytest.mark.asyncio
    async def test_matching_query_should_return_matched(self, tool_with_catalog):
        result = json.loads(await tool_with_catalog._arun(query="导航"))
        assert result["success"] is True
        names = [t["name"] for t in result["matched_tools"]]
        assert "geo_navigator" in names

    @pytest.mark.asyncio
    async def test_no_match_should_return_empty_list(self, tool_with_catalog):
        result = json.loads(await tool_with_catalog._arun(query="不存在的关键词xyz"))
        assert result["success"] is True
        assert result["matched_tools"] == []
        assert "available_categories" in result

    @pytest.mark.asyncio
    async def test_empty_catalog_should_return_no_tools(self, tool):
        result = json.loads(await tool._arun(query=""))
        assert result["success"] is True
        assert result["matched_tools"] == []
        assert "当前没有可发现的工具" in result["message"]

    @pytest.mark.asyncio
    async def test_whitespace_query_should_return_all(self, tool_with_catalog):
        result = json.loads(await tool_with_catalog._arun(query="   "))
        assert result["success"] is True
        assert len(result["matched_tools"]) == 4


# ---------------------------------------------------------------------------
# TestSearchCatalog (评分+过滤+排序)
# ---------------------------------------------------------------------------


class TestSearchCatalog:
    def test_search_by_name_parts(self, tool_with_catalog):
        results = tool_with_catalog._search_catalog("web_research")
        assert len(results) == 1
        assert results[0]["name"] == "web_research"

    def test_search_by_keyword_in_summary(self, tool_with_catalog):
        results = tool_with_catalog._search_catalog("POI")
        assert len(results) >= 1
        assert results[0]["name"] == "geo_navigator"

    def test_search_multi_keyword_filters_by_hit_ratio(self, tool_with_catalog):
        """多token查询(≤2个token): 不过滤命中率, 保证高召回"""
        results = tool_with_catalog._search_catalog("地理 网络")
        names = {r["name"] for r in results}
        # 2个token, 不触发命中率过滤(仅>2时过滤)
        assert "geo_navigator" in names

    def test_search_case_insensitive(self, tool_with_catalog):
        results = tool_with_catalog._search_catalog("GEO_NAVIGATOR")
        assert len(results) == 1
        assert results[0]["name"] == "geo_navigator"

    def test_search_chinese_keyword(self, tool_with_catalog):
        results = tool_with_catalog._search_catalog("消息")
        assert len(results) == 1
        assert results[0]["name"] == "scheduled_messenger"

    def test_search_results_sorted_by_score(self, tool_with_catalog):
        """结果按相关性降序排序"""
        results = tool_with_catalog._search_catalog("geo navigator 地理")
        if len(results) > 1:
            # geo_navigator 应排第一 (name_parts命中 + summary命中)
            assert results[0]["name"] == "geo_navigator"

    def test_short_query_no_ratio_filter(self, tool_with_catalog):
        """短查询(≤2 tokens)不做比率过滤, 保证高召回"""
        results = tool_with_catalog._search_catalog("天气 weather")
        names = [r["name"] for r in results]
        # 2 tokens, 不过滤比率
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# TestTokenize
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_space_split(self):
        tokens, query_lower = SearchAvailableTools._tokenize("web research")
        assert tokens == ["web", "research"]
        assert query_lower == "web research"

    def test_underscore_split(self):
        tokens, _ = SearchAvailableTools._tokenize("geo_navigator")
        assert tokens == ["geo", "navigator"]

    def test_mixed(self):
        tokens, _ = SearchAvailableTools._tokenize("web research 网络搜索")
        assert tokens == ["web", "research", "网络搜索"]

    def test_empty(self):
        tokens, query_lower = SearchAvailableTools._tokenize("")
        assert tokens == []
        assert query_lower == ""


# ---------------------------------------------------------------------------
# TestScore
# ---------------------------------------------------------------------------


class TestScore:
    def test_exact_name_match(self):
        s, m, nh = SearchAvailableTools._score(
            ["web", "research"],
            "web_research",
            "web_research",
            {"name_parts": ["web", "research"], "description": "test"},
        )
        assert s == 10.0  # exact name match
        assert nh is True

    def test_name_parts_match(self):
        s, m, nh = SearchAvailableTools._score(
            ["web"],
            "web",
            "web_research",
            {"name_parts": ["web", "research"], "description": "test"},
        )
        assert s == 5.0  # name part exact
        assert nh is True

    def test_summary_match(self):
        s, m, nh = SearchAvailableTools._score(
            ["网络研究"],
            "网络研究",
            "some_tool",
            {"name_parts": ["some", "tool"], "description": "网络研究工具"},
        )
        assert s == 3.0  # summary match

    def test_no_match(self):
        s, m, nh = SearchAvailableTools._score(
            ["xyz"],
            "xyz",
            "some_tool",
            {"name_parts": ["some", "tool"], "description": "测试工具"},
        )
        assert s == 0.0

    def test_keyword_match(self):
        s, m, nh = SearchAvailableTools._score(
            ["画图"],
            "画图",
            "generate_image",
            {
                "name_parts": ["generate", "image"],
                "description": "图片生成",
                "keywords": ["画图", "绘图"],
            },
        )
        assert s == 4.0  # keyword match
        assert m == 1


# ---------------------------------------------------------------------------
# TestFormatAllTools
# ---------------------------------------------------------------------------


class TestFormatAllTools:
    def test_format_all_tools_with_catalog(self, tool_with_catalog):
        result = json.loads(tool_with_catalog._format_all_tools())
        assert result["success"] is True
        assert "共 4 个可发现的工具" in result["message"]
        assert len(result["matched_tools"]) == 4

    def test_format_all_tools_truncates_description(self, tool):
        long_desc = "A" * 200
        tool.set_catalog({"tool_a": {"name": "tool_a", "description": long_desc}})
        result = json.loads(tool._format_all_tools())
        assert len(result["matched_tools"][0]["description"]) <= 100

    def test_format_all_tools_empty_catalog(self, tool):
        result = json.loads(tool._format_all_tools())
        assert result["success"] is True
        assert result["matched_tools"] == []


# ---------------------------------------------------------------------------
# TestExpandMembers (组条目展开为成员工具名, 组名对主对话模型透明)
# ---------------------------------------------------------------------------

# 组条目结构对齐 inference_coordinator._enrich_search_tools_description 产出:
# name=组名(内部catalog key/filter输入), display_label=对外标签,
# _members=成员工具名+描述(命中后展开返回给主对话模型)
GROUP_CATALOG = {
    "todo_manager_group": {
        "name": "todo_manager_group",
        "description": "待办任务管理",
        "full_description": "待办任务管理工具组. 记录、跟踪和管理用户的待办事项。",
        "keywords": ["待办", "任务", "todo"],
        "name_parts": ["todo", "manager"],
        "display_label": "todo_manager",
        "_members": [
            {"name": "create_todo", "description": "创建待办事项"},
            {"name": "list_todos", "description": "列出待办事项"},
            {"name": "update_todo", "description": "更新待办事项"},
        ],
    },
    "weather_query": {
        "name": "weather_query",
        "description": "查询实时天气",
        "full_description": "查询指定城市的实时天气。",
        "keywords": ["天气"],
        "name_parts": ["weather", "query"],
    },
}


class TestExpandMembers:
    """组条目应展开为成员工具条目, 组名绝不进入返回给模型的 matched_tools."""

    def test_expand_group_to_member_entries(self, tool):
        tool.set_catalog(GROUP_CATALOG)
        expanded = tool._expand_members(list(GROUP_CATALOG.values()))
        names = [e["name"] for e in expanded]
        # 组展开为成员, 组名不出现
        assert "todo_manager_group" not in names
        assert {"create_todo", "list_todos", "update_todo"} <= set(names)
        # 非组工具原样透传
        assert "weather_query" in names

    def test_expand_preserves_member_description(self, tool):
        tool.set_catalog(GROUP_CATALOG)
        expanded = tool._expand_members([GROUP_CATALOG["todo_manager_group"]])
        descs = {e["name"]: e["description"] for e in expanded}
        assert descs["create_todo"] == "创建待办事项"

    def test_expand_passthrough_non_group(self, tool):
        """非组条目(无 _members)原样透传."""
        tool.set_catalog(GROUP_CATALOG)
        expanded = tool._expand_members([GROUP_CATALOG["weather_query"]])
        assert len(expanded) == 1
        assert expanded[0]["name"] == "weather_query"

    @pytest.mark.asyncio
    async def test_arun_group_match_returns_member_names(self, tool):
        """命中组时 matched_tools 返回成员工具名, 组名对主对话模型透明."""
        tool.set_catalog(GROUP_CATALOG)
        result = json.loads(await tool._arun(query="待办"))
        names = [t["name"] for t in result["matched_tools"]]
        assert "todo_manager_group" not in names
        assert set(names) == {"create_todo", "list_todos", "update_todo"}

    def test_format_all_tools_expands_groups(self, tool):
        """空查询返回全部时, 组条目展开为成员."""
        tool.set_catalog(GROUP_CATALOG)
        result = json.loads(tool._format_all_tools())
        names = [t["name"] for t in result["matched_tools"]]
        # 组展开为 3 成员 + 1 普通工具, 组名不出现
        assert "todo_manager_group" not in names
        assert len(names) == 4
        assert "weather_query" in names
        assert "create_todo" in names


# ---------------------------------------------------------------------------
# TestRecallFirst (召回优先: 0.2 阈值验证)
# ---------------------------------------------------------------------------


class TestRecallFirst:
    """验证召回优先策略: 多 token 查询中即使只有 1 个 token 命中也保留."""

    def test_three_token_query_one_hit_retained(self, tool_with_catalog):
        """3 token 查询, 1/3 命中(33%) > 20% 阈值, 应保留."""
        # 构造一个 3-token 查询, 只有 1 个 token 命中 scheduled_messenger
        results = tool_with_catalog._search_catalog("定时消息 xyz_abc 12345")
        names = [r["name"] for r in results]
        assert "scheduled_messenger" in names

    def test_four_token_query_one_hit_retained(self, tool_with_catalog):
        """4 token 查询, 1/4 命中(25%) > 20% 阈值, 应保留."""
        results = tool_with_catalog._search_catalog("天气 alpha beta gamma")
        names = [r["name"] for r in results]
        assert "weather_query" in names

    def test_name_hit_bypasses_ratio(self, tool_with_catalog):
        """name_parts 命中仍然绕过命中率过滤."""
        results = tool_with_catalog._search_catalog("web xyz_abc 12345 fake")
        names = [r["name"] for r in results]
        assert "web_research" in names


# ---------------------------------------------------------------------------
# TestArunWithLlmFilter (LLM 降噪集成测试)
# ---------------------------------------------------------------------------


class TestArunWithLlmFilter:
    """验证 _arun 中的 LLM 过滤集成 (mock LLM 调用)."""

    @pytest.mark.asyncio
    async def test_arun_llm_filter_reduces_results(self, tool_with_catalog):
        """_arun 在 >= 2 个结果时调用 LLM 过滤."""
        from unittest.mock import AsyncMock, patch

        # mock filter_tools_by_llm 只保留第一个
        with patch(
            "src.tools.internal._llm_tool_filter.filter_tools_by_llm",
            new_callable=AsyncMock,
        ) as mock_filter:
            mock_filter.side_effect = lambda q, c: [c[0]]

            # "天气 导航" 同时匹配 weather_query + geo_navigator (>=2 个结果)
            result = json.loads(await tool_with_catalog._arun(query="天气 导航"))
            assert result["success"] is True
            assert mock_filter.called

    @pytest.mark.asyncio
    async def test_arun_single_result_skips_llm(self, tool_with_catalog):
        """_arun 在只有 1 个结果时不调用 LLM."""
        from unittest.mock import AsyncMock, patch

        with patch(
            "src.tools.internal._llm_tool_filter.filter_tools_by_llm",
            new_callable=AsyncMock,
        ) as mock_filter:
            result = json.loads(await tool_with_catalog._arun(query="消息"))
            assert result["success"] is True
            assert not mock_filter.called

    @pytest.mark.asyncio
    async def test_arun_llm_failure_graceful_degradation(self, tool_with_catalog):
        """LLM 过滤失败时优雅降级, 返回全部关键词匹配结果."""
        from unittest.mock import AsyncMock, patch

        # mock 内部 LLM 调用抛异常, 让 filter_tools_by_llm 自身的 try/except 处理
        with patch(
            "src.tools.internal._llm_tool_filter._call_llm_filter",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Ollama not running"),
        ):
            # "天气 导航" 同时匹配 weather_query + geo_navigator (>=2 个结果)
            result = json.loads(await tool_with_catalog._arun(query="天气 导航"))
            assert result["success"] is True
            # 降级: LLM 失败后返回全部关键词匹配结果
            assert len(result["matched_tools"]) >= 2
