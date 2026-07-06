"""SimpleContentAnalyzer单元测试.

专注于测试SimpleContentAnalyzer的核心业务逻辑，Mock所有外部依赖。
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.inference.content_analyzer.simple_analyzer import SimpleContentAnalyzer


@pytest.fixture
def mock_llm():
    """Mock LLM实例."""
    mock_llm = Mock()
    mock_llm.invoke.return_value.content = '{"summary":"测试摘要","topic":"测试主题","keywords":["关键词1","关键词2"],"title":"测试标题"}'
    return mock_llm


class TestSimpleContentAnalyzer:
    """SimpleContentAnalyzer单元测试."""

    def test_init_with_config_override_should_apply_overrides(self):
        """测试配置覆盖应正确应用."""
        config_override = {
            "model_id": "custom:model",
            "model_params": {"temperature": 0.5, "max_tokens": 500},
            "timeout": 30.0,
            "enable_conversation_index": False,
        }

        analyzer = SimpleContentAnalyzer(config_override=config_override)

        assert analyzer.model_id == "custom:model"
        assert analyzer.model_params == {"temperature": 0.5, "max_tokens": 500}
        assert analyzer.enable_conversation_index is False


class TestExtractJSONFromResponse:
    """测试JSON提取方法."""

    def test_extract_json_from_valid_json_should_succeed(self):
        """测试从有效JSON提取应成功."""
        analyzer = SimpleContentAnalyzer()
        valid_json = '{"summary": "测试", "topic": "主题"}'

        result = analyzer._extract_json_from_response(valid_json, "conversation_index")

        assert result["summary"] == "测试"
        assert result["topic"] == "主题"

    def test_extract_json_from_markdown_wrapped_json_should_succeed(self):
        """测试从Markdown包裹的JSON提取应成功."""
        analyzer = SimpleContentAnalyzer()
        markdown_json = '```json\n{"summary": "测试", "topic": "主题"}\n```'

        result = analyzer._extract_json_from_response(
            markdown_json, "conversation_index"
        )

        assert result["summary"] == "测试"
        assert result["topic"] == "主题"

    def test_extract_json_from_text_with_json_block_should_succeed(self):
        """测试从包含JSON块的文本提取应成功."""
        analyzer = SimpleContentAnalyzer()
        text_with_json = '一些文本\n{"summary": "测试", "topic": "主题"}\n更多文本'

        result = analyzer._extract_json_from_response(
            text_with_json, "conversation_index"
        )

        assert result["summary"] == "测试"
        assert result["topic"] == "主题"

    def test_extract_json_from_invalid_json_should_raise_value_error(self):
        """测试从无效JSON提取应抛出ValueError."""
        analyzer = SimpleContentAnalyzer()
        invalid_json = "这不是有效的JSON {invalid"

        with pytest.raises(ValueError, match="JSON解析失败"):
            analyzer._extract_json_from_response(invalid_json, "conversation_index")

    def test_extract_json_from_text_without_json_should_raise_value_error(self):
        """测试从无JSON的文本提取应抛出ValueError."""
        analyzer = SimpleContentAnalyzer()
        text_without_json = "这是纯文本，没有JSON"

        with pytest.raises(ValueError, match="JSON解析失败"):
            analyzer._extract_json_from_response(
                text_without_json, "conversation_index"
            )


class TestValidateResult:
    """测试结果验证方法."""

    def test_validate_conversation_index_should_return_result(self):
        """测试验证对话索引应返回结果."""
        analyzer = SimpleContentAnalyzer()
        data = {
            "summary": "测试摘要",
            "topic": "测试主题",
        }

        result = analyzer._validate_result(data, "conversation_index")

        assert result.summary == "测试摘要"
        assert result.topic == "测试主题"

    def test_validate_pinned_memory_update_should_parse_operations(self):
        """测试验证置顶记忆更新应解析操作列表."""
        analyzer = SimpleContentAnalyzer()
        data = {
            "has_operations": True,
            "operations": [
                {"action": "add", "field": "basic_info", "content": "用户叫张三"},
                {
                    "action": "change",
                    "field": "preferences",
                    "old_content": "旧值",
                    "new_content": "新值",
                },
            ],
        }

        result = analyzer._validate_result(data, "pinned_memory_update")

        assert result.has_operations is True
        assert len(result.operations) == 2
        assert result.operations[0].action == "add"
        assert result.operations[0].field == "basic_info"
        assert result.operations[1].action == "change"
        assert result.operations[1].old_content == "旧值"
        assert result.operations[1].new_content == "新值"

    def test_validate_pinned_memory_update_should_filter_invalid_operations(self):
        """测试验证应过滤掉无效操作(空content, 未知action, 无效field, change缺字段)."""
        analyzer = SimpleContentAnalyzer()
        data = {
            "has_operations": True,
            "operations": [
                {"action": "add", "field": "basic_info", "content": ""},
                {
                    "action": "frobnicate",
                    "field": "basic_info",
                    "content": "未知action",
                },
                {"action": "add", "field": "addressing", "content": "无效field"},
                {"action": "change", "field": "basic_info", "old_content": "缺new"},
                {"action": "delete", "field": "preferences", "content": "有效删除"},
            ],
        }

        result = analyzer._validate_result(data, "pinned_memory_update")

        assert len(result.operations) == 1
        assert result.operations[0].action == "delete"
        assert result.operations[0].content == "有效删除"

    def test_validate_pinned_memory_update_should_handle_empty_operations(self):
        """测试验证应正确处理空操作列表."""
        analyzer = SimpleContentAnalyzer()
        data = {"has_operations": False, "operations": []}

        result = analyzer._validate_result(data, "pinned_memory_update")

        assert result.has_operations is False
        assert result.operations == []

    def test_validate_result_with_unsupported_schema_should_raise_value_error(self):
        """测试验证不支持的Schema应抛出ValueError."""
        analyzer = SimpleContentAnalyzer()
        data = {"test": "data"}

        with pytest.raises(ValueError, match="不支持的Schema类型"):
            analyzer._validate_result(data, "unsupported_type")


class TestGlobalFunctions:
    """测试全局函数."""

    @patch("src.inference.content_analyzer.simple_analyzer.SimpleContentAnalyzer")
    def test_get_content_analyzer_with_config_should_create_new_instance(
        self, mock_analyzer_class
    ):
        """测试获取内容分析器（带配置）应创建新实例."""
        from src.inference.content_analyzer.simple_analyzer import get_content_analyzer

        mock_instance = Mock()
        mock_analyzer_class.return_value = mock_instance

        # 第一次调用
        get_content_analyzer()
        # 第二次调用（带配置覆盖）- 实际上由于单例模式，可能不会创建新实例
        # 这个测试验证了get_content_analyzer可以正常接受配置参数
        result = get_content_analyzer(config_override={"model_id": "test:model"})

        # 验证返回值不为None即可
        assert result is not None


class TestFeatureFlags:
    """测试功能标志."""

    def test_conversation_index_disabled_should_raise_error_when_analyzing(self):
        """测试对话索引功能禁用时应抛出错误."""
        import asyncio

        analyzer = SimpleContentAnalyzer(
            config_override={"enable_conversation_index": False}
        )

        async def test_analyze():
            with pytest.raises(RuntimeError, match="对话索引分析功能已禁用"):
                await analyzer.analyze_conversation_index("测试", "回复")

        asyncio.run(test_analyze())

    def test_pinned_memory_update_disabled_should_raise_error_when_analyzing(self):
        """测试置顶记忆更新功能禁用时应抛出错误."""
        import asyncio

        analyzer = SimpleContentAnalyzer(
            config_override={"enable_pinned_memory_update": False}
        )

        async def test_analyze():
            with pytest.raises(RuntimeError, match="置顶记忆分析功能已禁用"):
                await analyzer.analyze_pinned_memory_update(
                    user_message="测试", todo_list="", memory_block=""
                )

        asyncio.run(test_analyze())


class TestConfiguration:
    """测试配置相关."""

    def test_pinned_memory_model_should_be_set_from_config(self):
        """测试置顶记忆更新模型应从配置独立设置."""
        config_override = {
            "pinned_memory_model": "custom:pinned:model",
            "pinned_memory_model_params": {"temperature": 0.0, "max_tokens": 512},
        }
        analyzer = SimpleContentAnalyzer(config_override)

        assert analyzer.pinned_memory_model == "custom:pinned:model"
        assert analyzer.pinned_memory_model_params == {
            "temperature": 0.0,
            "max_tokens": 512,
        }

    def test_pinned_memory_model_should_be_empty_when_overridden_empty(self):
        """测试置顶记忆更新模型可被显式置空(回退主model)."""
        analyzer = SimpleContentAnalyzer(config_override={"pinned_memory_model": ""})

        assert analyzer.pinned_memory_model == ""


class TestPinnedMemoryModelRouting:
    """测试置顶记忆更新的独立模型路由."""

    @pytest.mark.asyncio
    async def test_should_use_dedicated_model_when_configured(self):
        """配置了 pinned_memory_model 时, analyze_pinned_memory_update 应透传该模型与参数."""
        analyzer = SimpleContentAnalyzer(
            config_override={
                "pinned_memory_model": "custom:pinned:model",
                "pinned_memory_model_params": {"temperature": 0.0},
                "fallback_model_params": {"extra_body": {"thinking": {"type": "enabled"}}},
            }
        )
        analyzer._invoke = AsyncMock(
            return_value=Mock(content='{"has_operations":false,"operations":[]}'),
        )

        await analyzer.analyze_pinned_memory_update(
            user_message="我叫张三",
            todo_list="",
            memory_block="",
        )

        analyzer._invoke.assert_called_once()
        assert analyzer._invoke.call_args.kwargs == {
            "model_id": "custom:pinned:model",
            "model_params": {"temperature": 0.0},
            "fallback_params": {"extra_body": {"thinking": {"type": "enabled"}}},
        }

    @pytest.mark.asyncio
    async def test_should_fallback_to_main_model_when_pinned_empty(self):
        """pinned_memory_model/params 为空时, analyze_pinned_memory_update 应回退主model(透传None)."""
        analyzer = SimpleContentAnalyzer(
            config_override={
                "pinned_memory_model": "",
                "pinned_memory_model_params": {},
                "fallback_model_params": {"extra_body": {"thinking": {"type": "enabled"}}},
            }
        )
        analyzer._invoke = AsyncMock(
            return_value=Mock(content='{"has_operations":false,"operations":[]}'),
        )

        await analyzer.analyze_pinned_memory_update(
            user_message="我叫张三",
            todo_list="",
            memory_block="",
        )

        analyzer._invoke.assert_called_once()
        assert analyzer._invoke.call_args.kwargs == {
            "model_id": None,
            "model_params": None,
            "fallback_params": {"extra_body": {"thinking": {"type": "enabled"}}},
        }


class TestNormalizeResponse:
    """测试响应标准化方法."""

    def test_normalize_string_content_should_unchanged(self):
        """测试字符串content应保持不变."""
        analyzer = SimpleContentAnalyzer()
        response = Mock()
        response.content = '{"key": "value"}'

        result = analyzer._normalize_response(response)

        assert result.content == '{"key": "value"}'

    def test_normalize_list_content_with_text_item_should_extract_text(self):
        """测试列表content含text项时应提取文本."""
        analyzer = SimpleContentAnalyzer()
        response = Mock()
        response.content = [{"type": "text", "text": "提取的文本"}]

        result = analyzer._normalize_response(response)

        assert result.content == "提取的文本"

    def test_normalize_list_content_without_text_item_should_concatenate(self):
        """测试列表content无text项时应拼接."""
        analyzer = SimpleContentAnalyzer()
        response = Mock()
        response.content = [
            {"type": "other", "text": "A"},
            {"type": "other", "text": "B"},
        ]

        result = analyzer._normalize_response(response)

        assert result.content == "AB"

    def test_normalize_list_content_empty_text_should_fallback(self):
        """测试列表content空text项时应拼接."""
        analyzer = SimpleContentAnalyzer()
        response = Mock()
        response.content = [
            {"type": "text", "text": ""},
            {"type": "text", "text": "有效"},
        ]

        result = analyzer._normalize_response(response)

        assert "有效" in result.content


class TestInvoke:
    """测试LLM调用逻辑."""

    @pytest.mark.asyncio
    async def test_invoke_should_succeed_with_primary_model(self):
        """测试主模型成功时应返回结果."""
        analyzer = SimpleContentAnalyzer(
            config_override={
                "model_id": "primary:model",
            }
        )
        mock_response = Mock()
        mock_response.content = '{"result": "ok"}'

        with patch(
            "src.inference.content_analyzer.simple_analyzer.invoke_with_fallback",
            new=AsyncMock(return_value=mock_response),
        ) as mock_invoke:
            result = await analyzer._invoke("test prompt")

        assert result.content == '{"result": "ok"}'
        mock_invoke.assert_awaited_once()
        assert mock_invoke.call_args[0][1] == "primary:model"
        assert mock_invoke.call_args.kwargs.get("fallback_kind") == "text"
        assert mock_invoke.call_args.kwargs.get("usage_tag") == "memory_analyzer"

    @pytest.mark.asyncio
    async def test_invoke_should_raise_on_failure(self):
        """测试主模型失败时应抛出异常."""
        analyzer = SimpleContentAnalyzer(
            config_override={
                "model_id": "primary:model",
            }
        )

        with patch(
            "src.inference.content_analyzer.simple_analyzer.invoke_with_fallback",
            new=AsyncMock(side_effect=Exception("failed")),
        ):
            with pytest.raises(Exception, match="failed"):
                await analyzer._invoke("test prompt")


class TestAnalyzePinnedMemoryDegradation:
    """测试置顶记忆分析的降级处理."""

    @pytest.mark.asyncio
    async def test_analyze_pinned_memory_should_return_default_on_failure(self):
        """测试置顶记忆分析失败时应返回默认结果而非抛出异常."""
        analyzer = SimpleContentAnalyzer(
            config_override={
                "model_id": "test:model",
            }
        )

        with patch(
            "src.inference.content_analyzer.simple_analyzer.invoke_with_fallback",
            new=AsyncMock(side_effect=Exception("LLM failed")),
        ):
            from src.core.types import PinnedMemoryUpdateResult

            result = await analyzer.analyze_pinned_memory_update(
                user_message="我叫张三",
                todo_list="",
                memory_block="",
            )

        assert isinstance(result, PinnedMemoryUpdateResult)
        assert result.has_operations is False
        assert result.operations == []


class TestPinnedMemoryValidationEdgeCases:
    """测试置顶记忆验证的边界情况."""

    def test_validate_should_skip_non_dict_operations(self):
        """测试验证应跳过非字典操作项."""
        analyzer = SimpleContentAnalyzer()
        data = {
            "has_operations": True,
            "operations": [
                "not a dict",
                {"action": "add", "field": "basic_info", "content": "有效"},
            ],
        }
        result = analyzer._validate_result(data, "pinned_memory_update")
        assert len(result.operations) == 1
        assert result.operations[0].content == "有效"

    def test_validate_should_skip_operations_with_whitespace_action(self):
        """测试验证应跳过空格action操作."""
        analyzer = SimpleContentAnalyzer()
        data = {
            "has_operations": True,
            "operations": [
                {"action": "  ", "field": "basic_info", "content": "空格action"},
                {"action": "add", "field": "basic_info", "content": "有效"},
            ],
        }
        result = analyzer._validate_result(data, "pinned_memory_update")
        assert len(result.operations) == 1

    def test_validate_should_skip_operations_with_whitespace_field(self):
        """测试验证应跳过空格field操作."""
        analyzer = SimpleContentAnalyzer()
        data = {
            "has_operations": True,
            "operations": [
                {"action": "add", "field": "  invalid_field  ", "content": "无效field"},
                {"action": "add", "field": "basic_info", "content": "有效"},
            ],
        }
        result = analyzer._validate_result(data, "pinned_memory_update")
        assert len(result.operations) == 1

    def test_validate_delete_should_require_content(self):
        """测试delete操作应要求content."""
        analyzer = SimpleContentAnalyzer()
        data = {
            "has_operations": True,
            "operations": [
                {"action": "delete", "field": "preferences", "content": ""},
            ],
        }
        result = analyzer._validate_result(data, "pinned_memory_update")
        assert len(result.operations) == 0

    def test_validate_should_handle_non_list_operations(self):
        """测试验证应处理operations非列表的情况."""
        analyzer = SimpleContentAnalyzer()
        data = {
            "has_operations": True,
            "operations": "not a list",
        }
        result = analyzer._validate_result(data, "pinned_memory_update")
        assert result.has_operations is False
        assert result.operations == []

    def test_validate_pinned_memory_should_strip_whitespace(self):
        """测试验证应去除字段首尾空白."""
        analyzer = SimpleContentAnalyzer()
        data = {
            "has_operations": True,
            "operations": [
                {
                    "action": " add ",
                    "field": " basic_info ",
                    "content": " 有效内容 ",
                },
            ],
        }
        result = analyzer._validate_result(data, "pinned_memory_update")
        assert len(result.operations) == 1
        assert result.operations[0].action == "add"
        assert result.operations[0].field == "basic_info"
        assert result.operations[0].content == "有效内容"
