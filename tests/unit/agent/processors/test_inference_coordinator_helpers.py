"""InferenceCoordinator 辅助方法单元测试.

覆盖被遗漏的纯函数与小工具方法:
- _extract_tool_names / _enrich_search_tools_description
- _build_runnable_config / _ensure_callbacks_in_config
- _extract_agent_result / _strip_think_tags
- _extract_token_usage / _estimate_tokens_from_result
- _try_parse_accumulated_args / _extract_text_from_chunk
- _filter_think_tags_streaming
- _create_llm / _enable_tool_error_handling

Mock策略: Mock 外部依赖(create_llm/enable_tool_error_handling), 保留真实业务逻辑.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableConfig

from src.agent.processors.inference_coordinator import InferenceCoordinator


@pytest.fixture
def coordinator() -> InferenceCoordinator:
    return InferenceCoordinator({"llm": {"model": "test"}})


# ========== _extract_tool_names ==========


class TestExtractToolNames:
    def test_should_return_empty_when_no_tools_attr(self, coordinator):
        """配置对象无 tools 属性时应返回空列表."""
        config = SimpleNamespace()  # 无 tools 属性
        core, dormant = coordinator._extract_tool_names(config)
        assert core == []
        assert dormant == []

    def test_should_return_empty_when_config_falsy(self, coordinator):
        assert coordinator._extract_tool_names(None) == ([], [])

    def test_should_extract_core_and_dormant(self, coordinator):
        """应分别提取核心工具与可选(休眠)工具."""
        config = SimpleNamespace(
            tools=["todo", "memory_retrieval"],
            optional_tools=["weather", "export_document"],
        )
        core, dormant = coordinator._extract_tool_names(config)
        assert core == ["todo", "memory_retrieval"]
        assert dormant == ["weather", "export_document"]

    def test_should_default_dormant_empty_when_missing(self, coordinator):
        """缺少 optional_tools 时休眠列表应为空."""
        config = SimpleNamespace(tools=["todo"])
        core, dormant = coordinator._extract_tool_names(config)
        assert core == ["todo"]
        assert dormant == []


# ========== _enrich_search_tools_description ==========


def _make_tool(name: str, **kwargs) -> MagicMock:
    """构造带 name 属性的 Mock 工具(规避 Mock.name 描述符陷阱)."""
    tool = MagicMock()
    tool.name = name
    for k, v in kwargs.items():
        setattr(tool, k, v)
    return tool


class TestEnrichSearchToolsDescription:
    def test_should_noop_when_no_search_tool(self, coordinator):
        """核心工具中无 search_available_tools 时应直接返回."""
        core = [_make_tool("todo")]
        dormant = [_make_tool("weather")]
        coordinator._enrich_search_tools_description(core, dormant)
        # 无异常即通过, 未设置 catalog

    def test_should_noop_when_no_dormant_tools(self, coordinator):
        """休眠工具为空时应直接返回."""
        search = _make_tool("search_available_tools")
        coordinator._enrich_search_tools_description([search], [])
        search.set_catalog.assert_not_called()

    def test_should_set_catalog_and_enrich_description(self, coordinator):
        """应注入实例目录并更新 search 工具描述."""
        search = _make_tool("search_available_tools")
        dormant = [
            _make_tool(
                "weather_tool",
                summary="天气查询",
                description="查询天气预报\n详细说明",
                search_keywords=["天气", "weather"],
            ),
            _make_tool(
                "export_tool",
                description="导出文档",
                search_keywords=[],
            ),
        ]
        coordinator._enrich_search_tools_description([search], dormant)

        search.set_catalog.assert_called_once()
        catalog = search.set_catalog.call_args[0][0]
        assert "weather_tool" in catalog
        assert catalog["weather_tool"]["keywords"] == ["天气", "weather"]
        assert catalog["weather_tool"]["name_parts"] == ["weather", "tool"]
        # description 应被更新为包含休眠工具清单
        assert "weather_tool" in search.description
        assert "天气查询" in search.description

    def test_should_use_description_first_line_when_no_summary(self, coordinator):
        """无 summary 时应回退到 description 首行作为 tagline."""
        search = _make_tool("search_available_tools")
        dormant = [
            _make_tool("calc_tool", summary="", description="计算器工具\n第二行")
        ]
        coordinator._enrich_search_tools_description([search], dormant)
        assert "计算器工具" in search.description

    def test_should_build_group_entry_for_grouped_members(self, coordinator):
        """组成员应跳过独立catalog条目, 改由组条目代表检索."""
        search = _make_tool("search_available_tools")
        dormant = [
            _make_tool("schedule_message", summary="发送", description="创建定时消息"),
            _make_tool("list_scheduled", summary="查看", description="查看消息"),
            _make_tool("weather", summary="天气", description="天气查询"),
        ]
        groups = {
            "scheduled_messenger_group": SimpleNamespace(
                name="scheduled_messenger_group",
                summary="定时消息管理",
                keywords=["定时", "提醒"],
                members=["schedule_message", "list_scheduled"],
            ),
        }
        coordinator._enrich_search_tools_description(
            [search], dormant, tool_groups=groups
        )
        catalog = search.set_catalog.call_args[0][0]
        # 组成员不单独出现(由组条目代表检索)
        assert "schedule_message" not in catalog
        assert "list_scheduled" not in catalog
        # 组条目存在(组名仅作内部catalog key), 用组summary/keywords
        assert "scheduled_messenger_group" in catalog
        group_entry = catalog["scheduled_messenger_group"]
        assert group_entry["description"] == "定时消息管理"
        assert group_entry["keywords"] == ["定时", "提醒"]
        # name_parts 基于 display_label(去 _group 后缀), 不含 "group" 无义 token
        assert group_entry["name_parts"] == ["scheduled", "messenger"]
        # display_label + _members: 组命中后由search展开为成员工具名(组名对模型透明)
        assert group_entry["display_label"] == "scheduled_messenger"
        assert {m["name"] for m in group_entry["_members"]} == {
            "schedule_message",
            "list_scheduled",
        }
        # 非组工具正常出现
        assert "weather" in catalog
        # desc_lines 用 display_label, 组名不外泄给主对话模型
        assert "scheduled_messenger_group" not in search.description
        assert "- scheduled_messenger:" in search.description

    def test_should_keep_independent_entries_when_no_groups(self, coordinator):
        """无 tool_groups 时所有休眠工具独立建条目(向后兼容)."""
        search = _make_tool("search_available_tools")
        dormant = [_make_tool("tool_a", summary="A", description="A")]
        coordinator._enrich_search_tools_description([search], dormant)
        catalog = search.set_catalog.call_args[0][0]
        assert "tool_a" in catalog


# ========== _expand_group_names ==========


class TestExpandGroupNames:
    def test_should_return_unchanged_when_no_group_names(self):
        names = ["todo", "weather"]
        assert InferenceCoordinator._expand_group_names(names, {}) == names

    def test_should_expand_group_to_members(self):
        groups = {"g1": ["a", "b"]}
        assert InferenceCoordinator._expand_group_names(["g1"], groups) == ["a", "b"]

    def test_should_mix_groups_and_tools_preserving_order(self):
        groups = {"g1": ["a", "b"]}
        result = InferenceCoordinator._expand_group_names(
            ["todo", "g1", "weather"], groups
        )
        assert result == ["todo", "a", "b", "weather"]

    def test_should_deduplicate_overlapping_members(self):
        groups = {"g1": ["a", "b"], "g2": ["b", "c"]}
        result = InferenceCoordinator._expand_group_names(["g1", "g2"], groups)
        assert result == ["a", "b", "c"]

    def test_should_return_empty_for_empty_input(self):
        assert InferenceCoordinator._expand_group_names([], {"g1": ["a"]}) == []


# ========== _build_runnable_config ==========


class TestBuildRunnableConfig:
    def test_should_return_empty_config_when_no_callbacks(self, coordinator):
        """无 callbacks 时应返回空 RunnableConfig(dict)."""
        config = coordinator._build_runnable_config([], "u", "t", "a")
        assert isinstance(config, dict)

    def test_should_attach_callbacks_and_metadata(self, coordinator):
        """有 callbacks 时应设置 callbacks 与 metadata, 并回填每个回调的 metadata."""
        callback = MagicMock()
        callback.set_metadata = MagicMock()
        config = coordinator._build_runnable_config([callback], "u1", "t1", "a1")
        callback.set_metadata.assert_called_once_with({
            "user_id": "u1",
            "session_id": "t1",
            "agent_id": "a1",
        })
        assert config["callbacks"] == [callback]
        assert config["metadata"]["user_id"] == "u1"


# ========== _ensure_callbacks_in_config ==========


class TestEnsureCallbacksInConfig:
    def test_should_merge_when_existing_callbacks_is_list(self, coordinator):
        base = RunnableConfig(callbacks=[MagicMock()])
        existing = base["callbacks"]
        new_cb = MagicMock()
        result = coordinator._ensure_callbacks_in_config(base, [new_cb])
        assert result["callbacks"] == [*existing, new_cb]

    def test_should_replace_when_existing_callbacks_not_list(self, coordinator):
        base = RunnableConfig()
        base["callbacks"] = "not-a-list"
        new_cb = MagicMock()
        result = coordinator._ensure_callbacks_in_config(base, [new_cb])
        assert result["callbacks"] == [new_cb]


# ========== _extract_agent_result / _strip_think_tags ==========


class TestExtractAgentResult:
    def test_should_strip_think_tags_from_content(self, coordinator):
        """应从最后一条消息内容中移除 <think> 标签."""
        last = SimpleNamespace(content="<think>hidden</think>实际回复")
        result = coordinator._extract_agent_result({"messages": [last]})
        assert result == "实际回复"

    def test_should_return_str_when_no_content_attr(self, coordinator):
        """最后一条消息无 content 属性时应返回其字符串形式."""
        last = SimpleNamespace()
        result = coordinator._extract_agent_result({"messages": [last]})
        assert isinstance(result, str)

    def test_should_return_placeholder_when_empty_messages(self, coordinator):
        """messages 为空时应返回占位文本."""
        result = coordinator._extract_agent_result({"messages": []})
        assert "没有返回内容" in result

    def test_should_return_str_for_non_dict_result(self, coordinator):
        """非 dict 结果应返回字符串形式."""
        result = coordinator._extract_agent_result("plain string")
        assert result == "plain string"


class TestStripThinkTags:
    def test_should_remove_single_think_block(self):
        assert InferenceCoordinator._strip_think_tags("a<think>x</think>b") == "ab"

    def test_should_remove_multiline_block(self):
        content = "<think>\nline1\nline2\n</think>\nresult"
        assert InferenceCoordinator._strip_think_tags(content) == "result"

    def test_should_keep_text_without_tags(self):
        assert InferenceCoordinator._strip_think_tags("no tags here") == "no tags here"


# ========== _extract_token_usage / _estimate_tokens_from_result ==========


class TestExtractTokenUsage:
    def test_should_extract_from_usage_key(self, coordinator):
        result = {"usage": {"total_tokens": 100, "completion_tokens": 40}}
        assert coordinator._extract_token_usage(result) == (100, 40)

    def test_should_extract_from_token_usage_key(self, coordinator):
        result = {"token_usage": {"total_tokens": 200, "completion_tokens": 80}}
        assert coordinator._extract_token_usage(result) == (200, 80)

    def test_should_estimate_when_no_usage(self, coordinator):
        """无任何 usage 字段时应回退到估算."""
        total, response = coordinator._extract_token_usage({"messages": []})
        assert total >= 20
        assert response >= 10

    def test_should_estimate_for_non_dict_result(self, coordinator):
        total, response = coordinator._extract_token_usage("a result string")
        assert total >= 20
        assert response >= 10


class TestEstimateTokensFromResult:
    def test_should_floor_short_string_at_minimum(self):
        """短字符串应被抬升到最低阈值."""
        assert InferenceCoordinator._estimate_tokens_from_result("hi") == (20, 10)

    def test_should_scale_with_length(self):
        """长字符串应按 0.7 系数估算."""
        text = "x" * 100
        total, response = InferenceCoordinator._estimate_tokens_from_result(text)
        assert total == 70
        assert response == 60


# ========== _try_parse_accumulated_args ==========


class TestTryParseAccumulatedArgs:
    def test_should_return_empty_for_empty_string(self):
        assert InferenceCoordinator._try_parse_accumulated_args("") == {}

    def test_should_parse_valid_json_dict(self):
        raw = '{"city": "北京", "days": 3}'
        assert InferenceCoordinator._try_parse_accumulated_args(raw) == {
            "city": "北京",
            "days": 3,
        }

    def test_should_return_empty_for_invalid_json(self):
        assert InferenceCoordinator._try_parse_accumulated_args("not json") == {}

    def test_should_return_empty_for_non_dict_json(self):
        """解析结果非 dict 时应返回空."""
        assert InferenceCoordinator._try_parse_accumulated_args("[1, 2, 3]") == {}


# ========== _extract_text_from_chunk ==========


class TestExtractTextFromChunk:
    def test_should_extract_string_content(self, coordinator):
        chunk = AIMessageChunk(content="hello")
        assert coordinator._extract_text_from_chunk(chunk) == "hello"

    def test_should_return_none_for_empty_content(self, coordinator):
        chunk = AIMessageChunk(content="")
        assert coordinator._extract_text_from_chunk(chunk) is None

    def test_should_combine_text_blocks_from_list_content(self, coordinator):
        chunk = AIMessageChunk(
            content=[{"type": "text", "text": "foo"}, {"type": "text", "text": "bar"}]
        )
        assert coordinator._extract_text_from_chunk(chunk) == "foobar"

    def test_should_handle_string_parts_in_list(self, coordinator):
        chunk = AIMessageChunk(content=["part1", "part2"])
        assert coordinator._extract_text_from_chunk(chunk) == "part1part2"


# ========== _create_llm / _enable_tool_error_handling ==========


class TestCreateLlm:
    def test_should_pass_streaming_flag(self, coordinator):
        """应根据 llm_config 的 streaming 字段创建 LLM."""
        with patch(
            "src.agent.processors.inference_coordinator.create_llm"
        ) as mock_create:
            coordinator._create_llm("test-model", {"streaming": True})
            mock_create.assert_called_once_with("test-model", streaming=True)

    def test_should_default_streaming_false(self, coordinator):
        with patch(
            "src.agent.processors.inference_coordinator.create_llm"
        ) as mock_create:
            coordinator._create_llm("test-model", {})
            mock_create.assert_called_once_with("test-model", streaming=False)


class TestEnableToolErrorHandling:
    def test_should_delegate_to_shared_function(self):
        agent = MagicMock()
        with patch(
            "src.agent.processors.inference_coordinator.enable_tool_error_handling"
        ) as mock_enable:
            InferenceCoordinator._enable_tool_error_handling(agent)
            mock_enable.assert_called_once_with(agent)


# ========== _filter_by_capability ==========


class TestFilterByCapability:
    def test_should_return_all_when_model_caps_empty(self):
        """模型能力集为空时不过滤任何工具."""
        result = InferenceCoordinator._filter_by_capability(
            ["analyze_image", "weather_query"], set()
        )
        assert result == ["analyze_image", "weather_query"]

    @patch("src.agent.processors.inference_coordinator.get_tools_config")
    def test_should_filter_tool_with_matching_capability(self, mock_cfg):
        """skip_when_capabilities 与模型能力有交集时过滤."""
        def _lookup(name):
            if name == "analyze_image":
                return SimpleNamespace(skip_when_capabilities=["image_input"])
            return SimpleNamespace(skip_when_capabilities=[])

        mock_cfg.return_value.get_internal_tool_config = _lookup
        mock_cfg.return_value.get_external_tool_config = MagicMock(return_value=None)

        result = InferenceCoordinator._filter_by_capability(
            ["analyze_image", "weather_query"], {"image_input", "tool_calling"}
        )
        assert "analyze_image" not in result
        assert "weather_query" in result

    @patch("src.agent.processors.inference_coordinator.get_tools_config")
    def test_should_keep_tool_when_capability_not_in_model(self, mock_cfg):
        """skip_when_capabilities 与模型能力无交集时保留."""
        mock_cfg.return_value.get_internal_tool_config = MagicMock(
            return_value=SimpleNamespace(skip_when_capabilities=["image_input"])
        )
        mock_cfg.return_value.get_external_tool_config = MagicMock(return_value=None)

        result = InferenceCoordinator._filter_by_capability(
            ["analyze_image"], {"tool_calling"}
        )
        assert "analyze_image" in result

    @patch("src.agent.processors.inference_coordinator.get_tools_config")
    def test_should_keep_tool_with_empty_skip_caps(self, mock_cfg):
        """skip_when_capabilities 为空时始终保留."""
        mock_cfg.return_value.get_internal_tool_config = MagicMock(
            return_value=SimpleNamespace(skip_when_capabilities=[])
        )
        mock_cfg.return_value.get_external_tool_config = MagicMock(return_value=None)

        result = InferenceCoordinator._filter_by_capability(
            ["weather_query"], {"image_input"}
        )
        assert result == ["weather_query"]

    @patch("src.agent.processors.inference_coordinator.get_tools_config")
    def test_should_keep_unknown_tool(self, mock_cfg):
        """配置中不存在的工具名应保留(可能是 MCP/外部工具)."""
        mock_cfg.return_value.get_internal_tool_config = MagicMock(return_value=None)
        mock_cfg.return_value.get_external_tool_config = MagicMock(return_value=None)

        result = InferenceCoordinator._filter_by_capability(
            ["unknown_tool"], {"image_input"}
        )
        assert result == ["unknown_tool"]


# ========== _collect_prompt_hints ==========


class TestCollectPromptHints:
    @patch("src.agent.processors.inference_coordinator.get_tools_config")
    def test_should_collect_group_hint_from_original_names(self, mock_cfg):
        """组级 prompt_hint 从展开前的原始名收集."""
        group_cfg = SimpleNamespace(prompt_hint="组策略提示")
        tool_groups = {"todo_manager_group": group_cfg}

        mock_cfg.return_value.get_internal_tool_config = MagicMock(return_value=None)
        mock_cfg.return_value.get_external_tool_config = MagicMock(return_value=None)

        result = InferenceCoordinator._collect_prompt_hints(
            original_core=["todo_manager_group"],
            original_dormant=[],
            filtered_core=["create_todo", "list_todos"],
            filtered_dormant=[],
            tool_groups=tool_groups,
        )
        # 组名不外泄, 改用 display_label(去 _group 后缀) 作前缀
        assert "todo_manager_group" not in result
        assert "- todo_manager:" in result
        assert "组策略提示" in result

    @patch("src.agent.processors.inference_coordinator.get_tools_config")
    def test_should_collect_individual_hint_from_filtered_names(self, mock_cfg):
        """个体工具 prompt_hint 从过滤后的展开名收集."""
        tool_cfg = SimpleNamespace(prompt_hint="个体策略提示")
        mock_cfg.return_value.get_internal_tool_config = MagicMock(
            return_value=tool_cfg
        )
        mock_cfg.return_value.get_external_tool_config = MagicMock(return_value=None)

        result = InferenceCoordinator._collect_prompt_hints(
            original_core=[],
            original_dormant=[],
            filtered_core=["analyze_image"],
            filtered_dormant=[],
            tool_groups={},
        )
        assert "analyze_image" in result
        assert "个体策略提示" in result

    @patch("src.agent.processors.inference_coordinator.get_tools_config")
    def test_should_return_empty_when_no_hints(self, mock_cfg):
        """无 prompt_hint 时返回空字符串."""
        mock_cfg.return_value.get_internal_tool_config = MagicMock(
            return_value=SimpleNamespace(prompt_hint="")
        )
        mock_cfg.return_value.get_external_tool_config = MagicMock(return_value=None)

        result = InferenceCoordinator._collect_prompt_hints(
            original_core=[],
            original_dormant=[],
            filtered_core=["weather_query"],
            filtered_dormant=[],
            tool_groups={},
        )
        assert result == ""

    @patch("src.agent.processors.inference_coordinator.get_tools_config")
    def test_should_not_duplicate_group_and_member_hints(self, mock_cfg):
        """组名和成员工具名不重复收集."""
        group_cfg = SimpleNamespace(
            prompt_hint="组策略",
            members=["create_todo", "list_todos"],
        )
        tool_groups = {"todo_manager_group": group_cfg}

        member_cfg = SimpleNamespace(prompt_hint="成员策略")
        mock_cfg.return_value.get_internal_tool_config = MagicMock(
            return_value=member_cfg
        )
        mock_cfg.return_value.get_external_tool_config = MagicMock(return_value=None)

        result = InferenceCoordinator._collect_prompt_hints(
            original_core=["todo_manager_group"],
            original_dormant=[],
            filtered_core=["create_todo"],
            filtered_dormant=[],
            tool_groups=tool_groups,
        )
        # 组提示在前, 成员已被 seen 跳过
        assert result.count("- ") == 1
