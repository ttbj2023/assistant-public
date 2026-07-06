"""ProfessionalDatabaseTool 单元测试 - 外部工具, 独立client调DataPro + LLM整理.

测试范围:
1. _split_query_if_needed / _extract_result_text 纯函数
2. is_available 环境变量检查
3. _call_datapro (mock fastmcp.Client)
4. _light_process (mock ExpertModelFactory)
5. _arun 编排 (缓存命中/未命中/拆分/失败)

Mock策略: mock fastmcp.Client/ExpertModelFactory/get_semantic_cache, 避免真实调用.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.external.professional_database_tool import (
    ProfessionalDatabaseTool,
    _extract_result_text,
    _preprocess_response,
    _split_query_if_needed,
)


@pytest.fixture
def tool() -> ProfessionalDatabaseTool:
    return ProfessionalDatabaseTool()


# =============================================================================
# 纯函数测试
# =============================================================================


class TestSplitQuery:
    """_split_query_if_needed拆分逻辑."""

    def test_single_code_no_split(self):
        assert _split_query_if_needed("002594 ROE") == ["002594 ROE"]

    def test_company_name_no_split(self):
        assert _split_query_if_needed("比亚迪 ROE") == ["比亚迪 ROE"]

    def test_three_codes_no_split(self):
        assert len(_split_query_if_needed("002594 000858 600519 ROE")) == 1

    def test_four_codes_split_into_two(self):
        result = _split_query_if_needed("002594 000858 600519 000333 ROE")
        assert len(result) == 2
        assert "002594 000858 600519 ROE" in result
        assert "000333 ROE" in result

    def test_description_preserved_after_split(self):
        result = _split_query_if_needed(
            "002594.SZ 000858.SZ 600519.SH 000333.SZ 000568.SZ 季报"
        )
        assert len(result) == 2
        assert all("季报" in q for q in result)


class TestExtractResultText:
    """_extract_result_text文本提取."""

    def test_string_passthrough(self):
        assert _extract_result_text("raw text") == "raw text"

    def test_content_list_with_text_attr(self):
        result = MagicMock()
        result.content = [MagicMock(text="chunk1"), MagicMock(text="chunk2")]
        assert _extract_result_text(result) == "chunk1\nchunk2"

    def test_content_list_with_dict(self):
        result = MagicMock()
        result.content = [{"type": "text", "text": "hello"}]
        assert _extract_result_text(result) == "hello"

    def test_empty_returns_empty(self):
        assert _extract_result_text(None) == ""


# =============================================================================
# is_available 测试
# =============================================================================


class TestIsAvailable:
    """is_available环境变量检查."""

    @pytest.mark.asyncio
    async def test_available_when_key_set(self):
        with patch(
            "src.tools.external.professional_database_tool.get_credential",
            return_value="fake-key",
        ):
            t = ProfessionalDatabaseTool()
            assert await t.is_available() is True

    @pytest.mark.asyncio
    async def test_unavailable_when_key_missing(self):
        with patch(
            "src.tools.external.professional_database_tool.get_credential",
            return_value="",
        ):
            t = ProfessionalDatabaseTool()
            assert await t.is_available() is False


# =============================================================================
# _call_datapro 测试 (mock fastmcp.Client)
# =============================================================================


class TestCallDatapro:
    """_call_datapro独立client调用."""

    @pytest.mark.asyncio
    @patch("src.tools.external.professional_database_tool.get_credential")
    @patch("fastmcp.client.transports.StreamableHttpTransport")
    @patch("fastmcp.client.Client")
    async def test_should_call_tool_and_format(
        self, mock_client_cls, _mock_transport, mock_get_credential
    ):
        mock_get_credential.return_value = "fake-key"
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_result = MagicMock(
            content=[MagicMock(text='{"code":0,"msg":"success","items":[]}')]
        )
        mock_client.call_tool.return_value = mock_result
        mock_client_cls.return_value = mock_client

        t = ProfessionalDatabaseTool()
        result = await t._call_datapro("比亚迪 ROE")

        mock_client.call_tool.assert_awaited_once()
        assert "DataPro" in result or "success" in result or "items" in result

    @pytest.mark.asyncio
    async def test_should_return_error_when_key_missing(self):
        with patch(
            "src.tools.external.professional_database_tool.get_credential",
            return_value="",
        ):
            t = ProfessionalDatabaseTool()
            result = await t._call_datapro("query")
            assert "配置缺失" in result


# =============================================================================
# _light_process 测试 (mock ExpertModelFactory)
# =============================================================================


class TestLightProcess:
    """_light_process LLM整理."""

    @pytest.mark.asyncio
    @patch("src.tools.external.professional_database_tool.ExpertModelFactory")
    async def test_should_return_processed_text(self, mock_factory):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="整理后结果"))
        mock_factory.create_for_tool.return_value = mock_llm

        t = ProfessionalDatabaseTool()
        result = await t._light_process("raw data", "比亚迪 ROE")

        assert result == "整理后结果"

    @pytest.mark.asyncio
    @patch("src.tools.external.professional_database_tool.ExpertModelFactory")
    async def test_should_return_none_on_failure(self, mock_factory):
        mock_factory.create_for_tool.side_effect = RuntimeError("llm down")

        t = ProfessionalDatabaseTool()
        result = await t._light_process("raw data", "比亚迪 ROE")

        assert result is None


# =============================================================================
# _preprocess_response 测试 (结构驱动提取)
# =============================================================================


class TestPreprocess:
    """_preprocess_response结构驱动信息密度提取."""

    def test_finance_strips_null_and_unwraps_list(self):
        data = {
            "items": [
                {"证券代码": "002594", "table": {"ROE": [1.65], "空字段": [None]}}
            ]
        }
        result = _preprocess_response(json.dumps(data))
        assert "002594" in result
        assert "1.65" in result
        assert "None" not in result

    def test_enterprise_name_from_list_subitem(self):
        data = {
            "items": [
                {"企业风险信息": [{"企业名称": "腾讯", "风险类型描述": "行政处罚"}]}
            ]
        }
        result = _preprocess_response(json.dumps(data))
        assert "腾讯" in result
        assert "行政处罚" in result

    def test_json_string_field_parsed_as_list(self):
        change = json.dumps([
            {"change_item": "投资人变更", "change_time": "2020-01-01"}
        ])
        data = {"items": [{"公司名称": "华为", "工商变更记录(JSON字符串)": change}]}
        result = _preprocess_response(json.dumps(data))
        assert "工商变更记录" in result
        assert "投资人变更" in result

    def test_non_json_passthrough(self):
        result = _preprocess_response("纯文本非JSON")
        assert result == "纯文本非JSON"

    def test_strips_internal_keys(self):
        data = {
            "items": [{"公司名称": "华为", "公司ID": 123, "归属省份首字母小写": "h"}]
        }
        result = _preprocess_response(json.dumps(data))
        assert "华为" in result
        assert "123" not in result
        assert "首字母" not in result

    def test_finance_indicator_dict_full_expansion(self):
        """金融table(指标集合, >=10字段)全展开, 不受子项字段数限制."""
        indicators = {f"指标{i}": [float(i)] for i in range(15)}
        data = {"items": [{"证券代码": "002594", "table": indicators}]}
        result = _preprocess_response(json.dumps(data))
        for i in range(15):
            assert f"指标{i}" in result


# =============================================================================
# _arun 编排测试 (mock _call_datapro + _light_process + cache)
# =============================================================================

_LARGE_TEXT = "DATAPRO_RAW_" + "x" * 5000  # >4K触发DeepSeek


class TestArun:
    """_arun编排: 预处理→分流(小跳过DeepSeek/大DeepSeek整理)."""

    @pytest.mark.asyncio
    @patch.object(ProfessionalDatabaseTool, "_call_datapro", new_callable=AsyncMock)
    @patch.object(ProfessionalDatabaseTool, "_light_process", new_callable=AsyncMock)
    @patch("src.tools.external.professional_database_tool.get_semantic_cache")
    async def test_large_data_triggers_deepseek_and_writes_cache(
        self, mock_cache_fn, mock_light, mock_call
    ):
        mock_cache = AsyncMock()
        mock_cache.get.return_value = None
        mock_cache_fn.return_value = mock_cache
        mock_call.return_value = _LARGE_TEXT
        mock_light.return_value = "processed result"

        t = ProfessionalDatabaseTool()
        result = await t._arun("华为技术有限公司 工商信息")

        assert result == "processed result"
        mock_light.assert_awaited_once()
        mock_cache.put.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.object(ProfessionalDatabaseTool, "_call_datapro", new_callable=AsyncMock)
    @patch.object(ProfessionalDatabaseTool, "_light_process", new_callable=AsyncMock)
    @patch("src.tools.external.professional_database_tool.get_semantic_cache")
    async def test_small_data_skips_deepseek(
        self, mock_cache_fn, mock_light, mock_call
    ):
        mock_cache = AsyncMock()
        mock_cache.get.return_value = None
        mock_cache_fn.return_value = mock_cache
        mock_call.return_value = "small result"

        t = ProfessionalDatabaseTool()
        result = await t._arun("比亚迪 ROE")

        assert "small result" in result
        mock_light.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.object(ProfessionalDatabaseTool, "_call_datapro", new_callable=AsyncMock)
    @patch.object(ProfessionalDatabaseTool, "_light_process", new_callable=AsyncMock)
    @patch("src.tools.external.professional_database_tool.get_semantic_cache")
    async def test_cache_hit_skips_call(self, mock_cache_fn, mock_light, mock_call):
        cached = json.dumps({"result": "cached result"}, ensure_ascii=False)
        mock_cache = AsyncMock()
        mock_cache.get.return_value = cached
        mock_cache_fn.return_value = mock_cache

        t = ProfessionalDatabaseTool()
        result = await t._arun("比亚迪 ROE")

        assert result == "cached result"
        mock_call.assert_not_awaited()
        mock_light.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.object(ProfessionalDatabaseTool, "_call_datapro", new_callable=AsyncMock)
    @patch.object(ProfessionalDatabaseTool, "_light_process", new_callable=AsyncMock)
    @patch("src.tools.external.professional_database_tool.get_semantic_cache")
    async def test_llm_failure_fallback_to_preprocessed(
        self, mock_cache_fn, mock_light, mock_call
    ):
        mock_cache = AsyncMock()
        mock_cache.get.return_value = None
        mock_cache_fn.return_value = mock_cache
        mock_call.return_value = _LARGE_TEXT
        mock_light.return_value = None

        t = ProfessionalDatabaseTool()
        result = await t._arun("华为技术有限公司 工商信息")

        assert "DATAPRO_RAW_" in result
        mock_light.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.object(ProfessionalDatabaseTool, "_call_datapro", new_callable=AsyncMock)
    @patch.object(ProfessionalDatabaseTool, "_light_process", new_callable=AsyncMock)
    @patch("src.tools.external.professional_database_tool.get_semantic_cache")
    async def test_multi_batch_split(self, mock_cache_fn, mock_light, mock_call):
        mock_cache = AsyncMock()
        mock_cache.get.return_value = None
        mock_cache_fn.return_value = mock_cache
        mock_call.side_effect = ["batch1", "batch2"]

        t = ProfessionalDatabaseTool()
        result = await t._arun("002594 000858 600519 000333 ROE")

        assert mock_call.await_count == 2
        assert "batch1" in result and "batch2" in result
        mock_light.assert_not_awaited()
